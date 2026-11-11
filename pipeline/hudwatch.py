"""¿Está el marcador en pantalla? (EA FC lo quita en pausas, repeticiones, menús y al acabar).

Se muestrea la zona del marcador a 4 fps en miniatura y en gris. El aspecto «normal» del
marcador es el fotograma que más se parece al resto (el reloj cambia, pero el escudo, los
equipos y el fondo no). Cuando la zona deja de parecerse a él, el marcador se ha ido y el
Short deja de mostrar su copia (si no, se vería el menú o la jugada recortados ahí).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from . import media
from .paths import ffmpeg_bin

FPS = 4
SIZE = (64, 26)
PIX_TOL = 22  # diferencia de gris que aún cuenta como «el mismo píxel»
SAME = 0.5  # parecido mínimo (fracción de píxeles iguales) para que el marcador esté
MIN_SUPPORT = 0.2  # el aspecto de referencia tiene que verse en al menos el 20 % del clip


def _samples(src: str | Path, rect: list[float], start: float, dur: float, W: int, H: int) -> np.ndarray:
    x, y = int(rect[0] * W), int(rect[1] * H)
    w, h = max(2, int(rect[2] * W) // 2 * 2), max(2, int(rect[3] * H) // 2 * 2)
    sw, sh = SIZE
    raw = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
         "-i", str(src), "-an", "-vf", f"fps={FPS},crop={w}:{h}:{x}:{y},scale={sw}:{sh},format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, creationflags=media._NO_WINDOW,
    ).stdout
    n = len(raw) // (sw * sh)
    return np.frombuffer(raw[: n * sw * sh], np.uint8).reshape(n, sh, sw).astype(np.int16)


def presence_from_samples(a: np.ndarray) -> list[bool]:
    """Para cada muestra, si el marcador está. Sin un aspecto que se repita, se da por ausente."""
    n = len(a)
    if n == 0:
        return []
    flat = a.reshape(n, -1)
    sim = np.stack([(np.abs(flat - flat[i]) < PIX_TOL).mean(axis=1) for i in range(n)])
    support = (sim > 0.7).sum(axis=1)
    ref = int(np.argmax(support))
    if support[ref] < max(2, MIN_SUPPORT * n):
        return [False] * n
    on = sim[ref] >= SAME
    # Sin parpadeos: un cambio de estado tiene que durar al menos 2 muestras (0,5 s).
    out = on.copy()
    for i in range(1, n - 1):
        if on[i] != on[i - 1] and on[i] != on[i + 1]:
            out[i] = on[i - 1]
    return [bool(v) for v in out]


def presence(src: str | Path, rect: list[float], start: float, dur: float, W: int, H: int) -> list[tuple[float, bool]]:
    """[(segundo relativo a `start`, visible)] cada 1/FPS s."""
    on = presence_from_samples(_samples(src, rect, start, dur, W, H))
    return [((i + 0.5) / FPS, v) for i, v in enumerate(on)]


def enable_expr(samples: list[tuple[float, bool]], time_map=None) -> str | None:
    """Expresión `enable` de FFmpeg con los tramos (en tiempo del Short) en que el marcador se ve.
    None si se ve siempre; "0" si nunca. `time_map(t_rel)` → tiempo de salida o None si se corta."""
    if not samples:
        return None
    if all(v for _, v in samples):
        return None
    half = 0.5 / FPS
    spans: list[list[float]] = []
    for t, v in samples:
        if not v:
            continue
        a = time_map(t - half) if time_map else t - half
        b = time_map(t + half) if time_map else t + half
        if a is None or b is None:
            mid = time_map(t) if time_map else t
            if mid is None:
                continue
            a, b = (mid - half if a is None else a), (mid + half if b is None else b)
        a, b = max(0.0, a), max(0.0, b)
        if spans and a <= spans[-1][1] + 0.02:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    if not spans:
        return "0"
    spans[0][0] = 0.0 if spans[0][0] < half + 0.01 else spans[0][0]
    if all(v for _, v in samples[-2:]):
        spans[-1][1] = 1e6  # visible hasta el final
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans)
