"""Episodio de YouTube a partir de un directo largo (plan ClipForge, fase 2).

1. Señales del VOD (las mismas que «momentos», con caché) → los mejores tramos.
2. Cada buen tramo se amplía con contexto hasta formar capítulos de 1-6 min, y se eligen
   capítulos hasta llegar a la duración objetivo (en orden cronológico).
3. Se transcriben solo esos capítulos y la IA (opcional) les pone título, propone el título del
   vídeo, el texto de la miniatura, la descripción y los tags.
4. Render: intro con el mejor momento (10-15 s) + capítulos sin silencios (> 1,5 s), 16:9,
   subtítulos en .srt aparte, miniatura 1280x720 y descripción con los capítulos desde 00:00.

El plan se guarda en `output/<directo>/youtube/plan.json` y se puede editar antes de renderizar.
"""
from __future__ import annotations

import copy
import json
import logging
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import highlights as hl
from . import llm, media, subtitles
from .paths import FONTS_DIR, OUTPUT_DIR, WORK_DIR, ffmpeg_bin
from .presets import deep_merge
from .transcribe import Word, words_from_json, words_to_json

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]

DEFAULTS: dict = {
    "target_min": 20,  # duración aproximada del episodio
    "pad": 75.0,  # contexto antes y después de cada buen tramo
    "min_chapter": 60.0,
    "max_chapter": 360.0,
    "intro": True,
    "intro_len": 12.0,
    "cut_silences": True,
    "silence_min": 1.5,  # el plan: silencios de más de 1,5 s
    "silence_db": -35.0,
}

SYSTEM = (
    "Eres editor de un canal de gaming en YouTube. Escribes títulos de capítulo, títulos de vídeo, "
    "textos de miniatura y descripciones en el español del streamer. Respondes solo con el JSON pedido."
)

EPISODE_PROPS = {
    "titulo": {"type": "string"},
    "miniatura": {"type": "string"},
    "descripcion": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
    "capitulos": {"type": "array", "items": {"type": "string"}},
}


@dataclass
class Chapter:
    start: float
    end: float
    title: str = ""
    score: float = 0.0
    keep: bool = True

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class EpisodePlan:
    src: str
    duration: float
    chapters: list[Chapter]
    intro: list[float] | None = None  # [inicio, fin] del mejor momento
    thumb_t: float = 0.0
    thumb_text: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    cfg: dict = field(default_factory=dict)

    @property
    def out_dir(self) -> Path:
        return hl.vod_out_dir(Path(self.src)) / "youtube"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EpisodePlan":
        d = dict(d)
        d["chapters"] = [Chapter(**c) for c in d.get("chapters") or []]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, out_dir: Path | None = None) -> Path:
        out = out_dir or self.out_dir
        out.mkdir(parents=True, exist_ok=True)
        path = out / "plan.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        return path


def config(preset: dict) -> dict:
    return deep_merge(DEFAULTS, preset.get("episode") or {})


# ---------------------------------------------------------------- elegir capítulos

def pick_chapters(windows: list[hl.Window], duration: float, cfg: dict) -> list[Chapter]:
    """Los mejores tramos, ampliados con contexto y fusionados, hasta ~1,25 × la duración objetivo
    (el corte de silencios la reduce después). Devuelve capítulos en orden cronológico."""
    pad, lo, hi = float(cfg["pad"]), float(cfg["min_chapter"]), float(cfg["max_chapter"])
    budget = float(cfg["target_min"]) * 60 * 1.25
    spans: list[list[float]] = []  # [inicio, fin, mejor puntuación]
    for w in sorted(windows, key=lambda w: w.score, reverse=True):
        if sum(b - a for a, b, _ in spans) >= budget:
            break
        a, b = max(0.0, w.start - pad), min(duration, w.end + pad)
        merged = False
        for s in spans:
            if a <= s[1] + 30 and b >= s[0] - 30:  # se toca con uno ya elegido: lo amplía
                if max(b, s[1]) - min(a, s[0]) <= hi:
                    s[0], s[1], s[2] = min(a, s[0]), max(b, s[1]), max(s[2], w.score)
                merged = True
                break
        if not merged:
            spans.append([a, b, w.score])
    # Ampliar con contexto puede pasarse bastante: se quitan los capítulos más flojos mientras sobre.
    while len(spans) > 1 and sum(b - a for a, b, _ in spans) > budget * 1.15:
        weakest = min(spans, key=lambda s: s[2])
        if sum(b - a for a, b, _ in spans) - (weakest[1] - weakest[0]) < budget * 0.6:
            break
        spans.remove(weakest)
    chapters = [Chapter(round(a, 2), round(b, 2), score=round(sc, 2)) for a, b, sc in sorted(spans)]
    return [c for c in chapters if c.duration >= min(lo, duration)]


def pick_intro(src: Path, windows: list[hl.Window], duration: float, length: float,
               tries: int = 6) -> tuple[hl.Window, list[float]]:
    """El mejor momento para la intro, saltándose los que son un menú o una pausa (imagen quieta):
    un grito con el juego en pausa no sirve de gancho."""
    from .silence import detect_still

    ranked = sorted(windows, key=lambda w: w.score, reverse=True)[:tries]
    fallback = None
    for w in ranked:
        rng = [round(max(0.0, w.peak - length * 0.65), 2), round(min(duration, w.peak + length * 0.35), 2)]
        fallback = fallback or (w, rng)
        try:
            still = detect_still(str(src), 1.0, rng[1] - rng[0], start=rng[0])
        except Exception:
            return w, rng
        if sum(e - s for s, e in still) <= 0.2 * (rng[1] - rng[0]):
            return w, rng
    return fallback if fallback else (windows[0], [0.0, min(duration, length)])


def episode_prompt(chapters: list[Chapter], words: list[Word], game: str, glossary: str) -> str:
    blocks = []
    for k, c in enumerate(chapters):
        text = hl.transcript_block(words, c.start, c.end, max_words=20)
        if len(text) > 2500:
            text = text[:2500] + " …"
        blocks.append(f"### Capítulo {k} ({hl.fmt_time(c.start)}–{hl.fmt_time(c.end)} del directo)\n{text}")
    return (
        f"Juego/canal: {game or '—'}. Glosario: {glossary or '—'}.\n"
        f"Estos {len(chapters)} capítulos, en orden, forman un episodio de YouTube sacado de un directo. "
        "Cada línea es «[segundo] lo que se dice».\n\n" + "\n\n".join(blocks) + "\n\n"
        "Genera:\n"
        f"- capitulos: exactamente {len(chapters)} títulos cortos (2-6 palabras), uno por capítulo y en orden, "
        "que digan lo que pasa (nada de «Parte 1»).\n"
        "- titulo: título del vídeo de YouTube, natural y con gancho, sin clickbait engañoso (máx. 70 caracteres).\n"
        "- miniatura: 2-4 palabras en MAYÚSCULAS para la miniatura.\n"
        "- descripcion: 2-3 frases.\n"
        "- tags: 8-12 etiquetas sin #."
    )


def call_llm(prompt: str, cfg_llm: dict, call=None) -> dict:
    schema = llm._schema(EPISODE_PROPS)
    if call is not None:
        return call(prompt, schema, cfg_llm)
    old = llm.SYSTEM
    llm.SYSTEM = SYSTEM
    try:
        return llm.CALLERS[cfg_llm.get("provider", "ninguno")](prompt, schema, cfg_llm)
    finally:
        llm.SYSTEM = old


def plan_episode(
    src: str | Path,
    preset: dict,
    chat_file: str | Path | None = None,
    progress: ProgressFn | None = None,
    call=None,
    overrides: dict | None = None,
) -> tuple[EpisodePlan, list[Word]]:
    """Analiza el directo y propone el episodio (capítulos, intro, miniatura, textos)."""
    report = progress or (lambda f, m: None)
    src = Path(src)
    cfg = deep_merge(config(preset), overrides or {})
    sig = hl.analyze_signals(src, preset, chat_file, report)  # informa de 0 a 0,4
    chapters = pick_chapters(sig.windows, sig.info.duration, cfg)
    if not chapters:
        raise ValueError("No se encontró ningún tramo con contenido para el episodio.")

    t0 = time.perf_counter()
    words = hl.transcribe_regions(src, sig.track, [(c.start, c.end) for c in chapters],
                                  dict(preset["transcription"]), lambda f, m: report(0.40 + 0.45 * f, m))
    sig.timings["transcripcion"] = round(time.perf_counter() - t0, 1)

    il = float(cfg["intro_len"])
    best, intro = pick_intro(src, sig.windows, sig.info.duration, il)
    plan = EpisodePlan(str(src), sig.info.duration, chapters, intro if cfg["intro"] else None,
                       thumb_t=best.peak, notes=sig.notes, timings=sig.timings, cfg=cfg)
    for k, c in enumerate(chapters):
        c.title = f"Parte {k + 1}"

    provider = preset["llm"].get("provider", "ninguno")
    if call is not None or provider != "ninguno":
        report(0.88, f"La IA ({provider}) titula los capítulos…")
        t0 = time.perf_counter()
        try:
            data = call_llm(episode_prompt(chapters, words, preset.get("name", ""),
                                           preset["transcription"].get("glossary", "")), preset["llm"], call)
            for c, t in zip(chapters, data.get("capitulos") or []):
                if str(t).strip():
                    c.title = str(t).strip()
            plan.title = str(data.get("titulo") or "").strip()
            plan.thumb_text = str(data.get("miniatura") or "").strip().upper()
            plan.description = str(data.get("descripcion") or "").strip()
            plan.tags = [str(t).strip().lstrip("#") for t in data.get("tags") or [] if str(t).strip()]
        except Exception as e:
            log.warning("LLM episodio: %s", e)
            plan.notes.append(f"IA no disponible ({e}): los capítulos se llaman «Parte N».")
        plan.timings["ia"] = round(time.perf_counter() - t0, 1)
    else:
        plan.notes.append("Sin IA: los capítulos se llaman «Parte N». Cámbiales el nombre antes de renderizar.")
    plan.title = plan.title or f"{preset.get('name', 'Directo').upper()} · {time.strftime('%d/%m/%Y', time.localtime(src.stat().st_mtime))}"
    plan.thumb_text = plan.thumb_text or "MOMENTAZOS"

    out = plan.out_dir
    plan.save(out)
    (out / "palabras.json").write_text(json.dumps(words_to_json(words), ensure_ascii=False), encoding="utf-8")
    report(1.0, f"Episodio propuesto: {len(chapters)} capítulos.")
    return plan, words


def load_plan(out_dir: Path) -> tuple[EpisodePlan, list[Word]]:
    plan = EpisodePlan.from_dict(json.loads((out_dir / "plan.json").read_text(encoding="utf-8")))
    wp = out_dir / "palabras.json"
    words = words_from_json(json.loads(wp.read_text(encoding="utf-8"))) if wp.exists() else []
    return plan, words


# ---------------------------------------------------------------- miniatura y textos

def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("Montserrat-Black.ttf", "Nunito-Black.ttf"):
        p = FONTS_DIR / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        trial = f"{lines[-1]} {word}" if lines else word
        if lines and draw.textlength(trial, font=font) <= max_w:
            lines[-1] = trial
        else:
            lines.append(word)
    return lines


def make_thumbnail(src: str | Path, t: float, text: str, dst: Path, hook_style: dict | None = None,
                   handle: str = "") -> Path:
    """Miniatura 1280x720: fotograma del mejor momento + texto grande con contorno (estilo del gancho)."""
    style = hook_style or {}
    fill = style.get("fill", "#FFFFFF")
    outline = style.get("outline_color", "#E3262E")
    with tempfile.TemporaryDirectory(dir=WORK_DIR) as tmp:
        frame = media.extract_frame(src, Path(tmp) / "f.png", t)
        img = Image.open(frame).convert("RGB")
    W, H = 1280, 720
    scale = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    img = img.crop(((img.width - W) // 2, (img.height - H) // 2, (img.width - W) // 2 + W, (img.height - H) // 2 + H))
    # oscurecer la mitad izquierda para que el texto se lea
    shade = Image.new("L", (W, H))
    sd = ImageDraw.Draw(shade)
    for x in range(W):
        sd.line([(x, 0), (x, H)], fill=int(170 * max(0.0, 1 - x / (W * 0.75))))
    img = Image.composite(Image.new("RGB", (W, H), "black"), img, shade)
    draw = ImageDraw.Draw(img)
    text = (text or "").strip().upper()
    if text:
        size = 150
        while size > 60:
            font = _font(size)
            lines = _wrap(draw, text, font, int(W * 0.62))
            if len(lines) <= 3 and all(draw.textlength(l, font=font) <= W * 0.62 for l in lines):
                break
            size -= 10
        lh = int(size * 1.05)
        y = (H - lh * len(lines)) // 2
        for line in lines:
            draw.text((60, y), line, font=font, fill=fill, stroke_width=max(6, size // 12), stroke_fill=outline)
            y += lh
    if handle:
        f = _font(34)
        draw.text((60, H - 70), handle, font=f, fill="#FFFFFF", stroke_width=3, stroke_fill="#000000")
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=60))
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "PNG")
    return dst


def chapter_lines(starts: list[tuple[float, str]]) -> str:
    out = []
    for t, title in starts:
        s = int(round(t))
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        stamp = f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
        out.append(f"{stamp} {title}")
    return "\n".join(out)


def description_text(plan: EpisodePlan, starts: list[tuple[float, str]], handle: str = "") -> str:
    parts = [plan.title, ""]
    if plan.description:
        parts += [plan.description, ""]
    parts += ["Capítulos:", chapter_lines(starts), ""]
    if handle:
        parts += [f"Sígueme: {handle}", ""]
    if plan.tags:
        parts += ["Tags: " + ", ".join(plan.tags)]
    return "\n".join(parts).strip() + "\n"


# ---------------------------------------------------------------- render

def _episode_preset(preset: dict, cfg: dict, cut: bool) -> dict:
    p = copy.deepcopy(preset)
    p["mode"] = "largo"
    t = p["toggles"]
    t.update(subtitles=False, export_srt=True, hook_title=False, progress_bar=False, auto_zoom=False, sfx=False,
             cut_silences=bool(cut and cfg.get("cut_silences", True)))
    p["silences"] = dict(p.get("silences") or {}, source="audio", min_duration=float(cfg["silence_min"]),
                         threshold_db=float(cfg["silence_db"]), require_still=True)
    p["render"] = dict(p["render"], long_resolution="1080p")
    return p


def render_episode(plan: EpisodePlan, words: list[Word], preset: dict, progress: ProgressFn | None = None,
                   out_dir: Path | None = None) -> dict:
    """Renderiza intro + capítulos y los une. Devuelve las rutas de vídeo, srt, miniatura y descripción."""
    from .job import process_video

    report = progress or (lambda f, m: None)
    out = out_dir or plan.out_dir
    out.mkdir(parents=True, exist_ok=True)
    src = Path(plan.src)
    cfg = deep_merge(DEFAULTS, plan.cfg or {})
    kept = [c for c in plan.chapters if c.keep and c.duration > 3]
    if not kept:
        raise ValueError("No hay ningún capítulo marcado.")
    pieces: list[tuple[str, float, float, str]] = []  # (nombre, inicio, fin, título)
    if plan.intro:
        pieces.append(("intro", plan.intro[0], plan.intro[1], "Intro"))
    pieces += [(f"cap{k + 1:02d}", c.start, c.end, c.title or f"Parte {k + 1}") for k, c in enumerate(kept)]

    work = Path(tempfile.mkdtemp(prefix="episode_", dir=WORK_DIR))
    try:
        rendered: list[tuple[Path, float, str, list[Word]]] = []
        total_src = sum(e - s for _, s, e, _ in pieces) or 1.0
        done = 0.0
        for name, s, e, title in pieces:
            base, span = done / total_src, (e - s) / total_src
            report(0.02 + 0.85 * base, f"{title}: cortando {hl.fmt_time(s)}–{hl.fmt_time(e)}…")
            piece = hl.cut_clip(src, s, e, work / f"{name}_src.mp4", preset["render"].get("encoder", "auto"))
            p = _episode_preset(preset, cfg, cut=name != "intro")
            res = process_video(piece, p, progress=lambda f, m, b=base, sp=span, t=title: report(0.02 + 0.85 * (b + sp * f), f"{t}: {m}"),
                                words=hl.clip_words(words, s, e), output_dir=work, stem=name)
            pw = words_from_json(json.loads((work / f"{name}_words.json").read_text(encoding="utf-8")))
            rendered.append((res.video, media.probe(res.video).duration, title, pw))
            done += e - s

        report(0.9, "Uniendo capítulos…")
        listing = work / "concat.txt"
        listing.write_text("".join(f"file '{v.as_posix()}'\n" for v, _, _, _ in rendered), encoding="utf-8")
        video = out / "episodio.mp4"
        media.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                   "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(video)])

        starts: list[tuple[float, str]] = []
        all_words: list[Word] = []
        t = 0.0
        for _, dur, title, pw in rendered:
            starts.append((t, title))
            all_words += [Word(w.text, round(w.start + t, 3), round(w.end + t, 3), w.prob) for w in pw]
            t += dur
        # YouTube: el primero en 00:00 y capítulos de al menos 10 s
        starts = [st for i, st in enumerate(starts) if i == 0 or st[0] - starts[i - 1][0] >= 10]

        srt = subtitles.write_text(out / "episodio.srt", subtitles.build_srt(all_words))
        report(0.95, "Miniatura…")
        thumb = make_thumbnail(src, plan.thumb_t, plan.thumb_text, out / "miniatura.png",
                               preset.get("hook"), (preset.get("handle") or "").strip())
        desc = out / "descripcion.txt"
        desc.write_text(description_text(plan, starts, (preset.get("handle") or "").strip()), encoding="utf-8")
        plan.save(out)
        report(1.0, "¡Episodio listo!")
        return {"video": video, "srt": srt, "thumbnail": thumb, "description": desc,
                "duration": t, "chapters": starts}
    finally:
        shutil.rmtree(work, ignore_errors=True)
