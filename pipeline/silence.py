"""Corte de silencios: detección, tramos que se conservan y reajuste de tiempos.

Dos fuentes posibles:
- "audio":    filtro silencedetect de FFmpeg sobre la pista del micro (umbral en dB).
- "palabras": huecos entre palabras de la transcripción (VAD de Whisper).

El corte se hace en el mismo pase de render con select/aselect, y los tiempos de
los subtítulos se recalculan sobre la nueva línea de tiempo.
"""
from __future__ import annotations

import re
from dataclasses import replace

from . import media
from .paths import ffmpeg_bin
from .transcribe import Word

Segment = tuple[float, float]

_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences_audio(src: str, track: int, threshold_db: float, min_dur: float, duration: float) -> list[Segment]:
    out = media.run([
        ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(src), "-map", f"0:a:{track}", "-vn",
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_dur}", "-f", "null", "-",
    ]).stderr
    silences: list[Segment] = []
    start = None
    for line in out.splitlines():
        if (m := _START.search(line)):
            start = max(0.0, float(m.group(1)))
        elif (m := _END.search(line)) and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    if start is not None:
        silences.append((start, duration))
    return silences


def silences_from_words(words: list[Word], duration: float, min_dur: float) -> list[Segment]:
    if not words:
        return []
    gaps = [(0.0, words[0].start)]
    gaps += [(a.end, b.start) for a, b in zip(words, words[1:])]
    gaps.append((words[-1].end, duration))
    return [(a, b) for a, b in gaps if b - a >= min_dur]


def keep_segments(duration: float, silences: list[Segment], padding: float = 0.15, min_keep: float = 0.15) -> list[Segment]:
    """Tramos que se conservan: todo menos los silencios (recortados con un margen)."""
    cuts = []
    for s, e in sorted(silences):
        a = s + padding if s > 0.01 else 0.0  # silencio inicial: se corta desde el principio
        b = e - padding if e < duration - 0.01 else duration
        if b - a > 0.05:
            cuts.append((a, b))
    keeps: list[Segment] = []
    t = 0.0
    for a, b in cuts:
        if a - t >= min_keep:
            keeps.append((round(t, 3), round(a, 3)))
        t = max(t, b)
    if duration - t >= min_keep:
        keeps.append((round(t, 3), round(duration, 3)))
    return keeps


def total(segments: list[Segment]) -> float:
    return sum(b - a for a, b in segments)


def remap_time(t: float, segments: list[Segment]) -> float:
    """Tiempo original → tiempo en el vídeo cortado (los huecos colapsan a su borde)."""
    offset = 0.0
    for a, b in segments:
        if t < a:
            return offset
        if t <= b:
            return offset + (t - a)
        offset += b - a
    return offset


def remap_words(words: list[Word], segments: list[Segment]) -> list[Word]:
    out = []
    for w in words:
        s, e = remap_time(w.start, segments), remap_time(w.end, segments)
        if e - s > 0.02:  # palabras que caían enteras dentro de un corte desaparecen
            out.append(replace(w, start=round(s, 3), end=round(e, 3)))
    return out


def cut_points(segments: list[Segment]) -> list[float]:
    """Instantes (en el vídeo cortado) donde hay un salto."""
    points, t = [], 0.0
    for a, b in segments[:-1]:
        t += b - a
        points.append(round(t, 3))
    return points


def select_expr(segments: list[Segment]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in segments)
