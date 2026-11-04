"""Buscar momentos en un directo largo (VOD de 1-4 h) y convertirlos en clips.

Pasos:
1. Señales baratas sobre TODO el vídeo: volumen del micro (gritos, risas), cambios de escena
   y, si se da el archivo, mensajes de chat por minuto.
2. Se puntúa cada tramo de 30 s (puntuación robusta por señal × peso) y se quedan los mejores.
3. Transcripción: el VOD entero (con GPU NVIDIA) o solo los tramos candidatos ± contexto
   (sin GPU: 3 h de Whisper en CPU serían horas). Las palabras clave suman puntos.
4. El LLM lee la transcripción de cada tramo y devuelve los clips (inicio, fin, gancho, título,
   hashtags). Sin LLM, el clip se centra en el pico de volumen.
5. Cada clip se corta del VOD a un archivo pequeño y pasa por el editor de siempre
   (job.process_video), así que layout, subtítulos, gancho y efectos son los del preset.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable

import numpy as np

from . import llm, media
from .keywords import normalize
from .paths import CACHE_DIR, OUTPUT_DIR, WORK_DIR, ensure_dirs, ffmpeg_bin
from .presets import deep_merge
from .transcribe import Word, cuda_available, transcribe, words_from_json, words_to_json

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]

DEFAULTS: dict = {
    "max_clips": 8,
    "min_len": 20.0,  # segundos
    "max_len": 60.0,
    "window": 30.0,  # tamaño del tramo que se puntúa
    "candidates": 25,  # tramos que pasan al LLM
    "context": 60.0,  # segundos de transcripción antes y después de cada tramo
    "transcribe": "auto",  # auto | candidatos | todo
    "scenes": True,  # detectar cambios de escena (decodifica el vídeo a baja resolución)
    "scene_threshold": 0.35,
    "weights": {"volumen": 1.0, "picos": 1.0, "chat": 1.2, "escenas": 0.5, "palabras": 0.8},
    "keywords": (
        "no puede ser, mira esto, qué locura, que locura, no jodas, dios mío, madre mía, no no no, "
        "qué fue eso, que fue eso, wasted, gg, increíble, ayuda, corre, me muero, lo logré, por fin, "
        "noooo, vamos, épale, coño, chamo, verga, molleja"
    ),
}

SYSTEM = (
    "Eres editor de clips de gaming para TikTok, Reels, Shorts y X. Eliges los mejores momentos "
    "de un directo largo y escribes los textos en el español del streamer. Respondes solo con el JSON pedido."
)

CLIP_PROPS = {
    "clips": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "tramo": {"type": "integer"},
                "inicio": {"type": "number"},
                "fin": {"type": "number"},
                "puntuacion": {"type": "integer"},
                "por_que": {"type": "string"},
                "hook": {"type": "string"},
                "titulo": {"type": "string"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["tramo", "inicio", "fin", "puntuacion", "por_que", "hook", "titulo", "hashtags"],
            "additionalProperties": False,
        },
    },
}


@dataclass
class Window:
    start: float
    end: float
    score: float = 0.0
    signals: dict[str, float] = field(default_factory=dict)  # valor bruto de cada señal
    peak: float = 0.0  # segundo más fuerte del tramo


@dataclass
class Clip:
    start: float
    end: float
    score: float = 0.0  # 1-10
    why: str = ""
    hook: str = ""
    title: str = ""
    hashtags: list[str] = field(default_factory=list)
    keep: bool = True

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class HighlightResult:
    src: Path
    duration: float
    clips: list[Clip]
    windows: list[Window]
    words: list[Word]
    out_dir: Path
    notes: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    def save(self) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / "candidatos.json"
        path.write_text(json.dumps({
            "vod": str(self.src), "duracion": round(self.duration, 2),
            "clips": [asdict(c) for c in self.clips],
            "tramos": [asdict(w) for w in self.windows],
            "avisos": self.notes, "tiempos": self.timings,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        (self.out_dir / "palabras.json").write_text(
            json.dumps(words_to_json(self.words), ensure_ascii=False), encoding="utf-8")
        return path


def config(preset: dict) -> dict:
    return deep_merge(DEFAULTS, preset.get("highlights") or {})


def fmt_time(t: float) -> str:
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:04.1f}"


def parse_time(text: str | float | int) -> float:
    """«1:12:04.3», «12:04», «724.3» → segundos."""
    if isinstance(text, (int, float)):
        return float(text)
    parts = str(text).strip().replace(",", ".").split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p or 0)
    return total


# ---------------------------------------------------------------- señales

def loudness_per_second(src: str | Path, track: int, duration: float, progress: ProgressFn | None = None,
                        rate: int = 8000) -> np.ndarray:
    """dBFS (RMS) de cada segundo de la pista del micro, leyendo el audio en streaming."""
    proc = subprocess.Popen(
        [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(src), "-map", f"0:a:{track}", "-vn",
         "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out: list[float] = []
    chunk = rate * 2 * 60  # 60 s por lectura
    pending = b""
    assert proc.stdout is not None
    while True:
        data = proc.stdout.read(chunk)
        if not data:
            break
        pending += data
        n = len(pending) // (rate * 2)
        if n:
            a = np.frombuffer(pending[: n * rate * 2], dtype=np.int16).astype(np.float32) / 32768.0
            rms = np.sqrt(np.mean(a.reshape(n, rate) ** 2, axis=1) + 1e-12)
            out.extend((20 * np.log10(rms)).tolist())
            pending = pending[n * rate * 2:]
            if progress and duration:
                progress(min(len(out) / duration, 1.0), f"Midiendo el volumen… {len(out) // 60} / {duration / 60:.0f} min")
    proc.wait()
    if pending:
        a = np.frombuffer(pending[: len(pending) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        if a.size:
            out.append(float(20 * np.log10(np.sqrt(np.mean(a ** 2) + 1e-12))))
    return np.asarray(out, dtype=np.float32)


_PTS = re.compile(r"pts_time:([\d.]+)")


def scene_changes(src: str | Path, threshold: float = 0.35, duration: float = 0.0,
                  progress: ProgressFn | None = None) -> list[float]:
    """Segundos con un cambio de escena fuerte.

    Solo se decodifican los fotogramas clave (OBS pone uno cada ~2 s) a 160 px: unas 3 veces más
    rápido que decodificar todo, y de sobra para tramos de 30 s.
    """
    proc = subprocess.Popen(
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-loglevel", "info", "-an", "-sn", "-skip_frame", "nokey",
         "-i", str(src), "-vf", f"scale=160:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    times: list[float] = []
    assert proc.stderr is not None
    for line in proc.stderr:
        if "showinfo" in line and (m := _PTS.search(line)):
            t = float(m.group(1))
            times.append(t)
            if progress and duration:
                progress(min(t / duration, 1.0), f"Buscando cambios de escena… {t / 60:.0f} / {duration / 60:.0f} min")
    proc.wait()
    return times


def load_chat(path: str | Path) -> list[float]:
    """Segundos de cada mensaje de chat.

    Acepta la salida de chat-downloader (JSON con una lista o JSON Lines, campo `time_in_seconds`),
    o un texto/CSV con una marca de tiempo al principio de cada línea («01:12:04 usuario: hola»).
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    items: list | None = None
    if text[0] in "[{":
        try:
            data = json.loads(text)
            items = data if isinstance(data, list) else data.get("messages") or data.get("comments") or []
        except json.JSONDecodeError:
            try:
                items = [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")] or None
            except json.JSONDecodeError:
                items = None  # «[00:01:05] usuario: …» empieza por [ pero es texto
    if items is not None:
        out = []
        for it in items:
            for key in ("time_in_seconds", "content_offset_seconds", "offset", "t"):
                if isinstance(it, dict) and it.get(key) is not None:
                    try:
                        out.append(float(it[key]))
                    except (TypeError, ValueError):
                        pass
                    break
        return sorted(t for t in out if t >= 0)
    stamp = re.compile(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)\]?")
    return sorted(parse_time(m.group(1)) for line in text.splitlines() if (m := stamp.match(line.strip())))


def keyword_list(cfg: dict, glossary: str = "") -> list[str]:
    raw = f"{cfg.get('keywords', '')},{glossary or ''}"
    return [normalize(k) for k in re.split(r"[,;\n]+", raw) if len(normalize(k)) >= 2]


def keyword_hits(words: list[Word], keywords: list[str]) -> list[float]:
    """Segundos en que se dice una palabra o frase clave (o una palabra gritada/alargada: «noooo»)."""
    if not words or not keywords:
        return []
    norm = [normalize(w.text) for w in words]
    singles = {k for k in keywords if " " not in k}
    phrases = [k.split() for k in keywords if " " in k]
    hits: list[float] = []
    for i, n in enumerate(norm):
        if not n:
            continue
        if n in singles or re.search(r"([aeiou])\1{3,}", n) or (words[i].text.isupper() and len(n) >= 3):
            hits.append(words[i].start)
            continue
        for ph in phrases:
            if norm[i:i + len(ph)] == ph:
                hits.append(words[i].start)
                break
    return hits


# ---------------------------------------------------------------- puntuación

def _robust_z(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med))) * 1.4826
    scale = mad if mad > 1e-6 else (float(np.std(values)) or 1.0)
    return np.clip((values - med) / scale, -3.0, 5.0)


def _count_in(times: list[float] | np.ndarray, start: float, end: float) -> int:
    arr = np.asarray(times, dtype=np.float64)
    return int(np.searchsorted(arr, end) - np.searchsorted(arr, start))


def make_windows(duration: float, size: float = 30.0, stride: float | None = None) -> list[Window]:
    stride = stride or size / 2
    out, t = [], 0.0
    while t < duration - 1.0:
        out.append(Window(t, min(t + size, duration)))
        t += stride
    if not out and duration > 0:
        out.append(Window(0.0, duration))
    return out


def score_windows(windows: list[Window], loud: np.ndarray, weights: dict, chat: list[float] | None = None,
                  scenes: list[float] | None = None, keywords: list[float] | None = None) -> list[Window]:
    """Rellena signals/score/peak de cada tramo. Cada señal se normaliza sobre todo el VOD."""
    if loud.size:
        base = float(np.median(loud))
        loud_sorted = loud
    else:
        base, loud_sorted = -60.0, np.zeros(0)
    raw: dict[str, list[float]] = {k: [] for k in ("volumen", "picos", "chat", "escenas", "palabras")}
    for w in windows:
        a, b = int(w.start), max(int(w.start) + 1, int(np.ceil(w.end)))
        seg = loud_sorted[a:b]
        if seg.size:
            vol = float(np.percentile(seg, 90)) - base
            peaks = float(np.sum(seg > base + 12.0))
            w.peak = float(a + int(np.argmax(seg)))
            silent = float(np.mean(seg < base - 6.0))
        else:
            vol, peaks, silent, w.peak = 0.0, 0.0, 1.0, w.start
        w.signals = {
            "volumen": round(vol, 2), "picos": peaks,
            "chat": float(_count_in(chat, w.start, w.end)) if chat else 0.0,
            "escenas": float(_count_in(scenes, w.start, w.end)) if scenes else 0.0,
            "palabras": float(_count_in(keywords, w.start, w.end)) if keywords else 0.0,
            "silencio": round(silent, 2),
        }
        for k in raw:
            raw[k].append(w.signals[k])
    zs = {k: _robust_z(np.asarray(v, dtype=np.float64)) for k, v in raw.items()}
    for i, w in enumerate(windows):
        s = sum(float(weights.get(k, 0.0)) * float(zs[k][i]) for k in raw if np.any(np.asarray(raw[k]) != raw[k][0]))
        s -= 2.0 * max(0.0, w.signals["silencio"] - 0.6)  # tiempo muerto (sin voz ni juego)
        w.score = round(s, 3)
    return windows


def top_windows(windows: list[Window], n: int, min_gap: float = 30.0) -> list[Window]:
    """Los n mejores tramos sin solaparse (separados al menos min_gap s), ordenados por tiempo."""
    chosen: list[Window] = []
    for w in sorted(windows, key=lambda w: w.score, reverse=True):
        if all(w.end + min_gap <= c.start or w.start >= c.end + min_gap for c in chosen):
            chosen.append(w)
        if len(chosen) >= n:
            break
    return sorted(chosen, key=lambda w: w.start)


def regions(windows: list[Window], context: float, duration: float) -> list[tuple[float, float]]:
    """Tramos ± contexto, fusionando los que se tocan (lo que hay que transcribir)."""
    spans = sorted((max(0.0, w.start - context), min(duration, w.end + context)) for w in windows)
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 1.0:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


# ---------------------------------------------------------------- transcripción por tramos

def _region_key(src: Path, track: int, a: float, b: float, cfg: dict) -> str:
    st = src.stat()
    raw = json.dumps([str(src.resolve()), st.st_size, int(st.st_mtime), track, round(a, 2), round(b, 2),
                      cfg.get("model"), cfg.get("language"), cfg.get("glossary", ""), cfg.get("dialect", "latam"), 1],
                     ensure_ascii=False)
    return "reg_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def transcribe_regions(src: Path, track: int, spans: list[tuple[float, float]], cfg: dict,
                       progress: ProgressFn | None = None) -> list[Word]:
    """Transcribe solo los trozos indicados (con caché por trozo) y devuelve palabras con tiempos del VOD."""
    report = progress or (lambda f, m: None)
    total = sum(b - a for a, b in spans) or 1.0
    done = 0.0
    words: list[Word] = []
    work = Path(tempfile.mkdtemp(prefix="vodasr_", dir=WORK_DIR))
    try:
        for k, (a, b) in enumerate(spans):
            cache = CACHE_DIR / f"{_region_key(src, track, a, b, cfg)}.json"
            if cfg.get("reuse_cache", True) and cache.exists():
                part = words_from_json(json.loads(cache.read_text(encoding="utf-8")))
            else:
                wav = work / f"r{k}.wav"
                media.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
                           "-i", str(src), "-map", f"0:a:{track}", "-vn", "-ac", "1", "-ar", "16000",
                           "-c:a", "pcm_s16le", str(wav)])

                def sub(f: float, m: str, k=k, a=a, b=b, done=done):
                    report((done + f * (b - a)) / total, f"Transcribiendo tramo {k + 1}/{len(spans)}… {m}")

                part, _ = transcribe(wav, cfg, b - a, sub)
                part = [replace(w, start=round(w.start + a, 3), end=round(w.end + a, 3)) for w in part]
                cache.write_text(json.dumps(words_to_json(part), ensure_ascii=False), encoding="utf-8")
            words.extend(part)
            done += b - a
            report(done / total, f"Transcrito {done / 60:.1f} de {total / 60:.1f} min")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    words.sort(key=lambda w: w.start)
    return words


# ---------------------------------------------------------------- LLM

def transcript_block(words: list[Word], a: float, b: float, gap: float = 0.7, max_words: int = 14) -> str:
    """Transcripción de [a, b] en líneas «[segundos] frase» (corta en pausas o cada max_words palabras)."""
    sel = [w for w in words if a <= w.start < b]
    lines, cur, t0 = [], [], None
    for i, w in enumerate(sel):
        if cur and (w.start - sel[i - 1].end > gap or len(cur) >= max_words):
            lines.append(f"[{t0:.1f}] {' '.join(cur)}")
            cur = []
        if not cur:
            t0 = w.start
        cur.append(w.text)
    if cur:
        lines.append(f"[{t0:.1f}] {' '.join(cur)}")
    return "\n".join(lines) or "(sin voz)"


def clips_prompt(cands: list[Window], words: list[Word], cfg: dict, game: str, glossary: str,
                 duration: float) -> str:
    ctx = float(cfg["context"])
    blocks = []
    for k, w in enumerate(cands):
        a, b = max(0.0, w.start - ctx), min(duration, w.end + ctx)
        sig = ", ".join(f"{name}={val:g}" for name, val in w.signals.items())
        blocks.append(
            f"### Tramo {k} — momento fuerte entre {w.start:.0f} y {w.end:.0f} s "
            f"(texto de {a:.0f} a {b:.0f} s). Señales: {sig}\n{transcript_block(words, a, b)}"
        )
    return (
        f"Juego/canal: {game or '—'}. Glosario: {glossary or '—'}.\n"
        f"Directo de {duration / 60:.0f} min. Un detector automático (volumen del micro, chat, cambios de escena "
        f"y palabras clave) marcó estos {len(cands)} tramos. Cada línea es «[segundo del directo] lo que se dice».\n\n"
        + "\n\n".join(blocks)
        + "\n\nElige como mucho " + str(int(cfg["max_clips"])) + " clips, los mejores, ordenados de mejor a peor. Reglas:\n"
        f"- Duración entre {cfg['min_len']:.0f} y {cfg['max_len']:.0f} s. inicio y fin en segundos del directo, "
        "dentro del texto del tramo.\n"
        "- Se tiene que entender sin haber visto el directo: empieza justo antes de que pase algo, en una frase "
        "completa (usa el segundo de una línea), y termina después de la reacción o el remate.\n"
        "- Descarta tramos sin nada que contar (menús, cargas, charla sin gracia). Mejor pocos clips buenos.\n"
        "- puntuacion: 1-10. por_que: una frase con lo que pasa.\n"
        "- hook: 2-5 palabras en MAYÚSCULAS para la pantalla («3 INTENTOS. EL MISMO JEFE.»).\n"
        "- titulo: título para el post, natural, sin clickbait engañoso. hashtags: 4-6, con #."
    )


def call_llm(prompt: str, cfg_llm: dict, call: Callable[[str, dict, dict], dict] | None = None) -> dict:
    schema = llm._schema(CLIP_PROPS)
    if call is not None:
        return call(prompt, schema, cfg_llm)
    provider = cfg_llm.get("provider", "ninguno")
    old = llm.SYSTEM
    llm.SYSTEM = SYSTEM  # los proveedores leen el prompt de sistema del módulo
    try:
        return llm.CALLERS[provider](prompt, schema, cfg_llm)
    finally:
        llm.SYSTEM = old


def snap(clip: Clip, words: list[Word], duration: float, lo: float, hi: float) -> Clip | None:
    """Ajusta el clip a frases: empieza al inicio de una palabra tras una pausa y acaba tras una palabra."""
    start, end = max(0.0, clip.start), min(duration, clip.end)
    if end - start > hi:
        end = start + hi
    if end - start < lo:
        extra = lo - (end - start)
        start, end = max(0.0, start - extra * 0.3), min(duration, end + extra * 0.7)
    near = [w for w in words if start - 4.0 <= w.start <= start + 1.5]
    if near:
        # la palabra más cercana al inicio pedido que venga después de una pausa (≥ 0,35 s)
        idx = {id(w): i for i, w in enumerate(words)}
        def after_pause(w: Word) -> bool:
            i = idx[id(w)]
            return i == 0 or w.start - words[i - 1].end >= 0.35
        cands = [w for w in near if after_pause(w)] or near
        w0 = min(cands, key=lambda w: abs(w.start - start))
        start = max(0.0, w0.start - 0.25)
    inside = [w for w in words if start < w.end <= start + hi]
    tail = [w for w in inside if end - 1.5 <= w.end <= end + 3.0]
    if tail:
        end = min(duration, max(tail, key=lambda w: w.end).end + 0.5)
    end = min(end, start + hi, duration)
    if end - start < min(lo, duration) - 0.5:
        return None
    return replace(clip, start=round(start, 2), end=round(end, 2))


def _overlaps(c: Clip, others: list[Clip]) -> bool:
    return any(min(c.end, o.end) - max(c.start, o.start) > 0.3 * min(c.duration, o.duration) for o in others)


def clips_from_llm(data: dict, cands: list[Window], words: list[Word], cfg: dict, duration: float) -> list[Clip]:
    ctx = float(cfg["context"])
    out: list[Clip] = []
    for d in data.get("clips") or []:
        try:
            k = int(d.get("tramo", -1))
            a, b = parse_time(d["inicio"]), parse_time(d["fin"])
        except (KeyError, TypeError, ValueError):
            continue
        # Que no se salga del texto que vio: el tramo con el que más se solapa (el nº de tramo puede venir mal).
        spans = [(w.start - ctx, w.end + ctx) for w in cands]
        best = max(spans, key=lambda s: min(b, s[1]) - max(a, s[0]), default=None)
        if best is None or min(b, best[1]) - max(a, best[0]) <= 0:
            if not (0 <= k < len(cands)):
                continue
            best = spans[k]
        a, b = max(a, best[0]), min(b, best[1])
        if b <= a:
            continue
        tags = [t if t.startswith("#") else f"#{t}" for t in (str(x).strip().replace(" ", "") for x in d.get("hashtags") or []) if t]
        clip = Clip(a, b, float(d.get("puntuacion") or 0), str(d.get("por_que") or "").strip(),
                    str(d.get("hook") or "").strip(), str(d.get("titulo") or "").strip(), tags)
        clip = snap(clip, words, duration, float(cfg["min_len"]), float(cfg["max_len"]))
        if clip and not _overlaps(clip, out):
            out.append(clip)
        if len(out) >= int(cfg["max_clips"]):
            break
    return out


def clips_from_signals(cands: list[Window], words: list[Word], cfg: dict, duration: float) -> list[Clip]:
    """Sin LLM: un clip alrededor del pico de volumen de los mejores tramos."""
    lo, hi = float(cfg["min_len"]), float(cfg["max_len"])
    length = min(hi, max(lo, 35.0))
    out: list[Clip] = []
    best = max((w.score for w in cands), default=1.0) or 1.0
    for w in sorted(cands, key=lambda w: w.score, reverse=True):
        start = max(0.0, w.peak - length * 0.6)
        clip = Clip(start, min(duration, start + length), round(max(1.0, min(10.0, 10 * w.score / best)), 1),
                    why=f"Pico de volumen en {fmt_time(w.peak)} (" +
                        ", ".join(f"{k} {v:g}" for k, v in w.signals.items() if v and k != "silencio") + ")")
        clip = snap(clip, words, duration, lo, hi)
        if clip and not _overlaps(clip, out):
            out.append(clip)
        if len(out) >= int(cfg["max_clips"]):
            break
    return out


# ---------------------------------------------------------------- orquestación

def vod_out_dir(src: Path, root: Path = OUTPUT_DIR) -> Path:
    from .job import safe_stem

    stem = safe_stem(src)
    if re.match(r"\d{4}-\d{2}-\d{2}", stem):  # OBS ya pone la fecha en el nombre
        return root / stem
    day = time.strftime("%Y-%m-%d", time.localtime(src.stat().st_mtime))
    return root / f"{day}_{stem}"


def find_highlights(
    src: str | Path,
    preset: dict,
    chat_file: str | Path | None = None,
    progress: ProgressFn | None = None,
    call: Callable[[str, dict, dict], dict] | None = None,
    out_root: Path = OUTPUT_DIR,
) -> HighlightResult:
    """Analiza un VOD y propone clips. `call` permite inyectar un LLM falso en los tests."""
    from .job import mic_track, transcribe_video

    ensure_dirs()
    report = progress or (lambda f, m: None)
    src = Path(src)
    cfg = config(preset)
    notes: list[str] = []
    timings: dict[str, float] = {}

    def step(name: str, t0: float) -> None:
        timings[name] = round(time.perf_counter() - t0, 1)

    info = media.probe(src)
    if not info.audio_tracks:
        raise ValueError("El vídeo no tiene audio: no se pueden buscar momentos.")
    track = mic_track(preset, info)
    if len(info.audio_tracks) == 1:
        notes.append("El vídeo tiene una sola pista de audio: el volumen incluye el juego. "
                     "Graba el micro en una pista aparte en OBS para que acierte más.")

    t0 = time.perf_counter()
    loud = loudness_per_second(src, track, info.duration, lambda f, m: report(0.02 + 0.18 * f, m))
    step("volumen", t0)

    scenes: list[float] = []
    if cfg.get("scenes", True):
        t0 = time.perf_counter()
        try:
            scenes = scene_changes(src, float(cfg["scene_threshold"]), info.duration,
                                   lambda f, m: report(0.20 + 0.20 * f, m))
        except Exception as e:  # opcional
            notes.append(f"Cambios de escena no disponibles: {e}")
        step("escenas", t0)

    chat: list[float] = []
    if chat_file:
        try:
            chat = load_chat(chat_file)
            if not chat:
                notes.append("El archivo de chat no tiene mensajes con tiempo.")
        except Exception as e:
            notes.append(f"No se pudo leer el chat: {e}")

    weights = cfg["weights"]
    windows = score_windows(make_windows(info.duration, float(cfg["window"])), loud, weights, chat, scenes)

    mode = cfg.get("transcribe", "auto")
    if mode == "auto":
        mode = "todo" if (preset["transcription"].get("device") != "cpu" and cuda_available()) else "candidatos"
    kw = keyword_list(cfg, preset["transcription"].get("glossary", ""))
    n_cand = int(cfg["candidates"])
    t0 = time.perf_counter()
    if mode == "todo":
        words = transcribe_video(src, preset, lambda f, m: report(0.40 + 0.40 * f, m), info=info, notes=notes)
        windows = score_windows(windows, loud, weights, chat, scenes, keyword_hits(words, kw))
        cands = top_windows(windows, n_cand)
    else:
        pre = top_windows(windows, min(len(windows), n_cand * 2))
        spans = regions(pre, float(cfg["context"]), info.duration)
        tcfg = dict(preset["transcription"])
        words = transcribe_regions(src, track, spans, tcfg, lambda f, m: report(0.40 + 0.40 * f, m))
        hits = keyword_hits(words, kw)
        windows = score_windows(windows, loud, weights, chat, scenes, hits)
        # Fuera de lo transcrito no se sabe si hay palabras clave: solo compiten los tramos transcritos.
        pre_ids = {(w.start, w.end) for w in pre}
        cands = top_windows([w for w in windows if (w.start, w.end) in pre_ids], n_cand)
    step("transcripcion", t0)

    t0 = time.perf_counter()
    clips: list[Clip] = []
    provider = preset["llm"].get("provider", "ninguno")
    if call is not None or provider != "ninguno":
        report(0.82, f"La IA ({provider}) elige los clips de {len(cands)} tramos…")
        try:
            data = call_llm(clips_prompt(cands, words, cfg, preset.get("name", ""),
                                         preset["transcription"].get("glossary", ""), info.duration),
                            preset["llm"], call)
            clips = clips_from_llm(data, cands, words, cfg, info.duration)
            if not clips:
                notes.append("La IA no propuso ningún clip válido; se usan los picos de volumen.")
        except Exception as e:
            log.warning("LLM highlights: %s", e)
            notes.append(f"IA no disponible ({e}); se usan los picos de volumen.")
    else:
        notes.append("Sin IA: los clips se centran en los picos de volumen (elige Claude/Ollama en ⚙️ Avanzado "
                     "para que lea lo que dices y escriba gancho y título).")
    if not clips:
        clips = clips_from_signals(cands, words, cfg, info.duration)
    step("ia", t0)

    res = HighlightResult(src, info.duration, clips, cands, words, vod_out_dir(src, out_root), notes, timings)
    res.save()
    report(1.0, f"{len(clips)} clips propuestos.")
    return res


def cut_clip(src: Path, start: float, end: float, dst: Path, encoder: str = "auto") -> Path:
    """Corta [start, end] del VOD con todas las pistas de audio, a calidad alta (entrada del editor)."""
    media.run([
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src),
        "-t", f"{end - start:.3f}", "-map", "0:v:0", "-map", "0:a?",
        *media.video_encoder_args(encoder, 16), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-avoid_negative_ts", "make_zero", str(dst),
    ])
    return dst


def clip_words(words: list[Word], start: float, end: float) -> list[Word]:
    return [replace(w, start=round(max(0.0, w.start - start), 3), end=round(min(end, w.end) - start, 3), grp=-1)
            for w in words if w.start >= start - 0.05 and w.end <= end + 0.3]


def clip_meta(c: Clip) -> str:
    lines = [f"Título: {c.title}" if c.title else "", f"Gancho: {c.hook}" if c.hook else "",
             f"Por qué: {c.why}" if c.why else "", " ".join(c.hashtags)]
    return "\n".join(x for x in lines if x)


def slug(text: str, n: int = 24) -> str:
    s = normalize(text or "")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:n].strip("_") or "clip"


def render_clips(
    src: str | Path,
    clips: list[Clip],
    words: list[Word],
    preset: dict,
    out_dir: Path,
    progress: ProgressFn | None = None,
) -> list:
    """Corta cada clip del VOD y lo pasa por el editor normal. Devuelve [(clip, JobResult | Exception)]."""
    from .job import process_video

    report = progress or (lambda f, m: None)
    src = Path(src)
    sel = [c for c in clips if c.keep]
    results = []
    work = Path(tempfile.mkdtemp(prefix="vodclips_", dir=WORK_DIR))
    try:
        for i, c in enumerate(sel):
            m, s = divmod(int(c.start), 60)
            name = f"{i + 1:02d}_" + (slug(c.title or c.hook) if (c.title or c.hook) else f"{m // 60}h{m % 60:02d}m{s:02d}s")
            base = i / max(len(sel), 1)
            span = 1 / max(len(sel), 1)
            report(base, f"[{i + 1}/{len(sel)}] Cortando {fmt_time(c.start)}–{fmt_time(c.end)}…")
            try:
                piece = cut_clip(src, c.start, c.end, work / f"{name}.mp4", preset["render"].get("encoder", "auto"))
                res = process_video(
                    piece, preset, progress=lambda f, m, i=i, base=base, span=span: report(base + span * f, f"[{i + 1}/{len(sel)}] {m}"),
                    words=clip_words(words, c.start, c.end), output_dir=out_dir / "clips", stem=name,
                    hook_text=c.hook or None, metadata_text=clip_meta(c),
                )
                results.append((c, res))
            except Exception as e:
                log.exception("Clip %s", name)
                results.append((c, e))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    report(1.0, "Clips listos.")
    return results
