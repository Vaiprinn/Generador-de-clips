"""Auto-zoom en palabras clave o gritos.

Los gritos se detectan midiendo el volumen (RMS) de cada palabra en la pista del
micro: una palabra es grito si está `shout_db` dB por encima de la mediana.
El zoom es un "punch-in" rápido sobre el gameplay con zoompan de FFmpeg.
"""
from __future__ import annotations

import statistics
import wave
from pathlib import Path

from .transcribe import Word


def word_loudness_db(wav_path: Path, words: list[Word]) -> list[float]:
    import numpy as np

    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    out = []
    for w in words:
        seg = data[int(w.start * sr): max(int(w.end * sr), int(w.start * sr) + 1)]
        rms = float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0
        out.append(20 * np.log10(rms + 1e-9))
    return out


def shout_indices(loudness: list[float], rel_db: float = 8.0, min_abs_db: float = -30.0) -> set[int]:
    if not loudness:
        return set()
    med = statistics.median(loudness)
    return {i for i, db in enumerate(loudness) if db >= med + rel_db and db >= min_abs_db}


def zoom_times(words: list[Word], shouts: set[int], cfg: dict) -> list[float]:
    cands = []
    if cfg.get("on_keywords", True):
        cands += [w.start for w in words if w.hl]
    if cfg.get("on_shouts", True):
        cands += [words[i].start for i in shouts if i < len(words)]
    times: list[float] = []
    min_gap = float(cfg.get("min_gap", 2.0))
    for t in sorted(cands):
        if not times or t - times[-1] >= min_gap:
            times.append(round(t, 3))
    return times


RISE, HOLD, FALL = 0.10, 0.55, 0.35


def zoom_expr(times: list[float], amount: float = 1.15) -> str | None:
    """Expresión z(it) para zoompan: subida rápida, mantiene y vuelve suave."""
    if not times or amount <= 1.0:
        return None
    terms = [
        f"clip((it-{t:.3f})/{RISE},0,1)*clip(({t + RISE + HOLD + FALL:.3f}-it)/{FALL},0,1)"
        for t in times
    ]
    return f"1+{amount - 1:.3f}*min(1,{'+'.join(terms)})"


def zoompan_filter(expr: str, w: int, h: int, fps: str) -> str:
    return (
        f"zoompan=z='{expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps}"
    )
