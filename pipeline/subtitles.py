"""Generación de subtítulos: .ass (quemado) y .srt (aparte).

Estilos:
- "frase":     frases de 3–6 palabras, blanco con contorno negro fino y sombra.
- "resaltado": 2–5 palabras en minúsculas, blanco bold con sombra paralela y
               palabras clave en amarillo.
- "karaoke":   1–3 palabras en MAYÚSCULAS; la palabra que se dice cambia de color
               y hace un "pop" (115 %). Un evento Dialogue por palabra.
Además, el "título gancho" (estilo sticker) va en capas propias.

Los subtítulos solo se ven mientras se habla (tiempos por palabra) y desaparecen
en los silencios.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .transcribe import Word

# La interfaz de TikTok/Reels/Shorts tapa los 660 px de abajo y la columna derecha.
SAFE_BOTTOM_PX = 660
SAFE_RIGHT_PX = 140
SAFE_LEFT_PX = 80

STYLES = ("frase", "resaltado", "karaoke")

# Valores por defecto de cada estilo (la UI los aplica al cambiar de estilo).
STYLE_DEFAULTS: dict[str, dict] = {
    "frase": {
        "font": "Nunito Black", "font_size": 72, "case": "original", "outline": 4, "shadow": 3,
        "min_words": 3, "max_words": 5, "max_chars": 24, "fade_ms": 60,
    },
    "resaltado": {
        "font": "Nunito Black", "font_size": 76, "case": "minusculas", "outline": 1, "shadow": 6,
        "min_words": 2, "max_words": 5, "max_chars": 22, "fade_ms": 60,
    },
    "karaoke": {
        "font": "Montserrat Black", "font_size": 96, "case": "mayusculas", "outline": 6, "shadow": 3,
        "min_words": 1, "max_words": 3, "max_chars": 18, "fade_ms": 0,
    },
}

_STRONG_PUNCT = re.compile(r"[.!?…]+[\"')\]]*$")
_SOFT_PUNCT = re.compile(r"[,;:]+$")


@dataclass
class Phrase:
    words: list[Word]
    start: float
    end: float

    @property
    def raw_text(self) -> str:
        return " ".join(w.text for w in self.words)


def group_phrases(
    words: list[Word],
    min_words: int = 3,
    max_words: int = 6,
    max_chars: int = 28,
    gap_split: float = 0.45,
) -> list[list[Word]]:
    """Agrupa palabras en frases cortas.

    Corta cuando hay una pausa, un signo fuerte (. ! ?), una coma con la frase ya
    "mínima", o al llegar al máximo de palabras/caracteres.
    """
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            prev = cur[-1]
            chars = sum(len(x.text) + 1 for x in cur) + len(w.text)
            if (
                w.start - prev.end > gap_split
                or _STRONG_PUNCT.search(prev.text)
                or (_SOFT_PUNCT.search(prev.text) and len(cur) >= min_words)
                or len(cur) >= max_words
                or chars > max_chars
            ):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    if max_words <= 1:
        return groups
    return _merge_orphans(groups, max_words, max_chars, gap_split)


def _merge_orphans(groups, max_words, max_chars, gap_split):
    """Une frases de 1 palabra con la anterior si van seguidas y caben."""
    out: list[list[Word]] = []
    for g in groups:
        if out and len(g) == 1:
            prev = out[-1]
            chars = sum(len(x.text) + 1 for x in prev) + len(g[0].text)
            if (
                g[0].start - prev[-1].end <= gap_split
                and not _STRONG_PUNCT.search(prev[-1].text)
                and len(prev) < max_words + 1
                and chars <= max_chars + 6
            ):
                prev.extend(g)
                continue
        out.append(g)
    return out


def build_phrases(
    words: list[Word],
    cfg: dict,
    pad: float = 0.15,
    min_dur: float = 0.4,
) -> list[Phrase]:
    groups = group_phrases(
        words,
        min_words=int(cfg.get("min_words", 3)),
        max_words=int(cfg.get("max_words", 6)),
        max_chars=int(cfg.get("max_chars", 28)),
    )
    phrases: list[Phrase] = []
    for i, g in enumerate(groups):
        start = g[0].start
        end = max(g[-1].end + pad, start + min_dur)
        if i + 1 < len(groups):
            end = min(end, groups[i + 1][0].start - 0.02)
        phrases.append(Phrase(g, start, max(end, start + 0.1)))
    return phrases


def format_text(text: str, cfg: dict) -> str:
    if cfg.get("strip_punctuation", True):
        text = re.sub(r"[.,;:…\"]+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
    case = cfg.get("case", "original")
    if case == "minusculas":
        text = text.lower()
    elif case == "mayusculas":
        text = text.upper()
    return text


# ---------------------------------------------------------------- ASS básicos

def ass_color(color: str, alpha: int = 0) -> str:
    """'#RRGGBB' (o 'rgb(r,g,b)' / 'rgba(...)' de Gradio) → '&HAABBGGRR'."""
    r, g, b = parse_color(color)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def ass_inline_color(color: str) -> str:
    """Color para etiquetas en línea (\\1c&HBBGGRR&)."""
    r, g, b = parse_color(color)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def parse_color(color: str) -> tuple[int, int, int]:
    c = (color or "#FFFFFF").strip()
    if c.startswith("#"):
        c = c[1:]
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    m = re.match(r"rgba?\(([^)]*)\)", c)
    if m:
        parts = [float(p) for p in m.group(1).split(",")[:3]]
        return tuple(int(round(p)) for p in parts)  # type: ignore[return-value]
    raise ValueError(f"Color no válido: {color}")


def ass_time(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def _scale(height: int, short: bool) -> float:
    """Los tamaños del preset están pensados para 1920 (Short) o 1080 (Largo) de alto."""
    return height / (1920 if short else 1080)


def safe_position(cfg: dict, width: int, height: int, short: bool = True) -> tuple[int, int]:
    """Centro (x, y) del subtítulo, fuera de las zonas que tapa la interfaz de TikTok."""
    k = _scale(height, short)
    size = int(cfg.get("font_size", 80) * k)
    left, right = (SAFE_LEFT_PX, SAFE_RIGHT_PX) if short else (60, 60)
    x = (left + width - right) // 2
    y = int(cfg.get("pos_y", height // 2) * k)
    max_y = height - (SAFE_BOTTOM_PX if short else int(40 * k)) - size  # sitio para 2 líneas
    return x, max(size, min(y, max_y))


def _style_line(name, font, size, primary, outline_c, back_c, bold, italic, outline, shadow,
                align, ml, mr, mv) -> str:
    return "Style: " + ",".join(str(v) for v in [
        name, font, size, primary, primary, outline_c, back_c,
        -1 if bold else 0, -1 if italic else 0, 0, 0,
        100, 100, 0, 0, 1, outline, shadow, align, ml, mr, mv, 1,
    ])


# ---------------------------------------------------------------- texto por estilo

def _word_text(w: Word, cfg: dict) -> str:
    return ass_escape(format_text(w.text, cfg))


def _phrase_text(words: list[Word], cfg: dict, highlight: bool) -> str:
    primary = ass_inline_color(cfg.get("primary_color", "#FFFFFF"))
    hl = ass_inline_color(cfg.get("highlight_color", "#FFE14D"))
    parts = []
    for w in words:
        t = _word_text(w, cfg)
        if not t:
            continue
        parts.append(f"{{\\1c{hl}}}{t}{{\\1c{primary}}}" if highlight and w.hl else t)
    return " ".join(parts)


def _karaoke_text(words: list[Word], active: int, cfg: dict, highlight: bool, animate: bool) -> str:
    primary = ass_inline_color(cfg.get("primary_color", "#FFFFFF"))
    act = ass_inline_color(cfg.get("active_color", "#39E75F"))
    hl = ass_inline_color(cfg.get("highlight_color", "#FFE14D"))
    pop = int(cfg.get("pop_scale", 115))
    parts = []
    for j, w in enumerate(words):
        t = _word_text(w, cfg)
        if not t:
            continue
        if j == active:
            anim = f"\\t(0,80,\\fscx{pop}\\fscy{pop})\\t(80,170,\\fscx100\\fscy100)" if animate else ""
            parts.append(f"{{\\1c{act}\\fscx100\\fscy100{anim}}}{t}{{\\1c{primary}\\fscx100\\fscy100}}")
        elif highlight and w.hl:
            parts.append(f"{{\\1c{hl}}}{t}{{\\1c{primary}}}")
        else:
            parts.append(t)
    return " ".join(parts)


# ---------------------------------------------------------------- ASS completo

def build_ass(
    words: list[Word],
    cfg: dict,
    width: int = 1080,
    height: int = 1920,
    short: bool = True,
    highlight: bool = False,
    hook: dict | None = None,
    duration: float = 0.0,
    animate: bool = True,
    show_subtitles: bool = True,
) -> str:
    """Genera el .ass completo. `hook` = config del título gancho con "text" (o None)."""
    style = cfg.get("style", "frase")
    if style not in STYLES:
        style = "frase"
    k = _scale(height, short)
    size = int(cfg.get("font_size", 80) * k)
    left, right = (SAFE_LEFT_PX, SAFE_RIGHT_PX) if short else (60, 60)
    x, y = safe_position(cfg, width, height, short)
    shadow_alpha = 0x30 if style == "resaltado" else 0x60

    styles = [_style_line(
        "Sub", cfg.get("font", "Nunito Black"), size,
        ass_color(cfg.get("primary_color", "#FFFFFF")),
        ass_color(cfg.get("outline_color", "#000000")),
        ass_color(cfg.get("shadow_color", "#000000"), alpha=shadow_alpha),
        True, False, round(float(cfg.get("outline", 4)) * k, 1), round(float(cfg.get("shadow", 3)) * k, 1),
        5, left, right, 0,
    )]
    events: list[str] = []

    if show_subtitles and words:
        fade = int(cfg.get("fade_ms", 0)) if animate else 0
        fad = f"\\fad({fade},{fade // 2})" if fade > 0 else ""
        tag = f"{{\\pos({x},{y}){fad}}}"
        phrases = build_phrases(words, cfg)
        for p in phrases:
            if style == "karaoke":
                for j, w in enumerate(p.words):
                    start = p.start if j == 0 else w.start
                    end = p.words[j + 1].start if j + 1 < len(p.words) else p.end
                    if end - start < 0.01:
                        continue
                    text = _karaoke_text(p.words, j, cfg, highlight, animate)
                    if text:
                        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Sub,,0,0,0,,{{\\pos({x},{y})}}{text}")
            else:
                text = _phrase_text(p.words, cfg, highlight or style == "resaltado")
                if text:
                    events.append(f"Dialogue: 0,{ass_time(p.start)},{ass_time(p.end)},Sub,,0,0,0,,{tag}{text}")

    if hook and (hook.get("text") or "").strip():
        hs, he = _hook_events(hook, width, height, duration, animate)
        styles += hs
        events = he + events

    lines = [
        "[Script Info]",
        "; Generado por ClipForge",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
    ]
    return "\n".join(lines) + "\n"


def _hook_events(hook: dict, width: int, height: int, duration: float, animate: bool):
    """Título gancho estilo sticker: dos capas del mismo texto.

    Capa 0: contorno exterior blanco muy grueso (+ sombra).
    Capa 1: contorno rojo grueso encima → queda un anillo blanco por fuera.
    """
    k = height / 1920
    size = int(hook.get("font_size", 120) * k)
    red = float(hook.get("outline", 12)) * k
    outer = red + float(hook.get("outer", 10)) * k
    font = hook.get("font", "Montserrat Black")
    fill = ass_color(hook.get("fill", "#FFFFFF"))
    margin = 60
    styles = [
        _style_line("GanchoFondo", font, size, fill, ass_color(hook.get("outer_color", "#FFFFFF")),
                    ass_color("#000000", alpha=0x70), True, True, round(outer, 1), round(6 * k, 1),
                    5, margin, margin, 0),
        _style_line("Gancho", font, size, fill, ass_color(hook.get("outline_color", "#E3262E")),
                    ass_color("#000000", alpha=0xFF), True, True, round(red, 1), 0, 5, margin, margin, 0),
    ]
    text = (hook.get("text") or "").strip()
    if hook.get("uppercase", True):
        text = text.upper()
    text = ass_escape(text).replace("|", "\\N")
    dur = float(hook.get("duration", 2.5))
    end = duration if dur <= 0 and duration > 0 else (dur if dur > 0 else 3600.0)
    x = width // 2
    y = int(hook.get("pos_y") or 650)
    tilt = float(hook.get("tilt", 0) or 0)
    rot = f"\\frz{tilt:g}" if tilt else ""
    anim = "\\fscx60\\fscy60\\t(0,120,\\fscx110\\fscy110)\\t(120,220,\\fscx100\\fscy100)\\fad(0,150)" if animate else ""
    tag = f"{{\\pos({x},{y}){rot}{anim}}}"
    events = [
        f"Dialogue: {layer},{ass_time(0)},{ass_time(end)},{st},,0,0,0,,{tag}{text}"
        for layer, st in ((0, "GanchoFondo"), (1, "Gancho"))
    ]
    return styles, events


# ---------------------------------------------------------------- SRT

SRT_MAX_WORDS = 8
SRT_MAX_CHARS = 42


def srt_time(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(words: list[Word]) -> str:
    """SRT legible para YouTube: líneas más largas y el texto tal cual se transcribió."""
    phrases = build_phrases(words, {"min_words": 3, "max_words": SRT_MAX_WORDS, "max_chars": SRT_MAX_CHARS})
    blocks = []
    n = 0
    for p in phrases:
        text = " ".join(w.text.strip() for w in p.words if w.text.strip())
        if not text:
            continue
        n += 1
        blocks.append(f"{n}\n{srt_time(p.start)} --> {srt_time(p.end)}\n{text}\n")
    return "\n".join(blocks)


def write_text(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8", newline="\n")
    return path
