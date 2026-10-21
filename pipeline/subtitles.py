"""Generación de subtítulos: agrupado en frases, .ass (quemado) y .srt (aparte).

Fase 1 implementa el estilo "Frase": frases cortas que aparecen solo mientras se
habla (según los tiempos de cada palabra) y desaparecen en los silencios.
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


def build_phrases(words: list[Word], cfg: dict, pad: float = 0.15, min_dur: float = 0.4) -> list[Phrase]:
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


# ---------------------------------------------------------------- ASS

def ass_color(color: str, alpha: int = 0) -> str:
    """'#RRGGBB' (o 'rgb(r,g,b)' / 'rgba(...)' de Gradio) → '&HAABBGGRR'."""
    r, g, b = parse_color(color)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


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


def safe_position(cfg: dict, width: int, height: int, short: bool = True) -> tuple[int, int]:
    """Centro (x, y) del subtítulo, fuera de las zonas que tapa la interfaz de TikTok."""
    size = int(cfg.get("font_size", 80))
    left, right = (SAFE_LEFT_PX, SAFE_RIGHT_PX) if short else (60, 60)
    x = (left + width - right) // 2
    y = int(cfg.get("pos_y", height // 2))
    max_y = height - (SAFE_BOTTOM_PX if short else 40) - size  # deja sitio para 2 líneas
    return x, max(size, min(y, max_y))


def build_ass(
    phrases: list[Phrase],
    cfg: dict,
    width: int = 1080,
    height: int = 1920,
    short: bool = True,
) -> str:
    size = int(cfg.get("font_size", 80))
    left, right = (SAFE_LEFT_PX, SAFE_RIGHT_PX) if short else (60, 60)
    x, y = safe_position(cfg, width, height, short)
    style = ",".join(str(v) for v in [
        "Frase",
        cfg.get("font", "Nunito Black"),
        size,
        ass_color(cfg.get("primary_color", "#FFFFFF")),
        ass_color(cfg.get("primary_color", "#FFFFFF")),
        ass_color(cfg.get("outline_color", "#000000")),
        ass_color(cfg.get("shadow_color", "#000000"), alpha=0x60),
        -1, 0, 0, 0,  # Bold, Italic, Underline, StrikeOut
        100, 100, 0, 0,  # ScaleX, ScaleY, Spacing, Angle
        1,  # BorderStyle: contorno + sombra
        cfg.get("outline", 4),
        cfg.get("shadow", 3),
        5,  # Alignment: centro
        left, right, 0,
        1,  # Encoding
    ])
    fade = int(cfg.get("fade_ms", 0))
    fad = f"\\fad({fade},{fade // 2})" if fade > 0 else ""
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
        f"Style: {style}",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for p in phrases:
        text = format_text(p.raw_text, cfg)
        if not text:
            continue
        lines.append(
            f"Dialogue: 0,{ass_time(p.start)},{ass_time(p.end)},Frase,,0,0,0,,"
            f"{{\\pos({x},{y}){fad}}}{ass_escape(text)}"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- SRT

def srt_time(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(phrases: list[Phrase], cfg: dict) -> str:
    blocks = []
    n = 0
    for p in phrases:
        text = format_text(p.raw_text, cfg)
        if not text:
            continue
        n += 1
        blocks.append(f"{n}\n{srt_time(p.start)} --> {srt_time(p.end)}\n{text}\n")
    return "\n".join(blocks)


def write_text(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8", newline="\n")
    return path
