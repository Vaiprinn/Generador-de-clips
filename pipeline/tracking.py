"""Seguimiento de la acción: una «cámara virtual» que mueve el recorte vertical del gameplay.

1. Se lee el vídeo a pocos fps y en baja resolución (rápido incluso en CPU).
2. En cada muestra se busca dónde está la acción:
   - «movimiento»: centro de masas de lo que cambia entre fotogramas (vale para cualquier juego),
   - «indicador» (EA FC): además, el marcador de color que el juego pone sobre el jugador que
     controlas; si se ve, manda él (es donde está el balón casi siempre).
   Se ignoran las zonas del HUD (marcador, radar) y la webcam, que también se mueven.
3. El recorrido se suaviza (zona muerta + suavizado en los dos sentidos + velocidad máxima)
   para que no maree, y se escribe un archivo `sendcmd` que mueve la `x` del `crop` en FFmpeg.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

from .paths import ffmpeg_bin

SAMPLE_FPS = 6.0
WIDTH = 320  # ancho de análisis


def _frames(src: str | Path, start: float, dur: float, w: int, h: int, fps: float):
    proc = subprocess.Popen(
        [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(start, 0):.3f}", "-t", f"{dur:.3f}",
         "-i", str(src), "-an", "-sn", "-vf", f"fps={fps},scale={w}:{h}:flags=area", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    size = w * h * 3
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def _mask(w: int, h: int, exclude: list[list[float]]) -> np.ndarray:
    m = np.ones((h, w), np.float32)
    for fx, fy, fw, fh in exclude or []:
        x0, y0 = int(fx * w) - 2, int(fy * h) - 2
        m[max(0, y0):max(0, y0) + int(fh * h) + 4, max(0, x0):max(0, x0) + int(fw * w) + 4] = 0
    return m


# Colores en los que EA FC pinta el indicador del jugador controlado (según el mando / ajustes).
INDICATOR_COLORS = {
    "magenta": lambda r, g, b: (r > 170) & (b > 90) & (g < 110),
    "azul": lambda r, g, b: (b > 170) & (r < 90) & (g < 160),
    "amarillo": lambda r, g, b: (r > 200) & (g > 170) & (b < 80),
    "cian": lambda r, g, b: (g > 170) & (b > 170) & (r < 90),
}
IND_WIDTH = 960
JUMP = 0.3  # salto del indicador (fracción del ancho) que hay que confirmar con otra muestra
HOLD = 1.5  # segundos que la cámara espera quieta si deja de ver el indicador
CROWD_WINDOW = 0.22  # ancho (fracción) de la ventana con la que se busca el grupo de jugadores


def crowd_x(frame: np.ndarray, mask: np.ndarray) -> float | None:
    """Centro del grupo de jugadores en un partido (EA FC u otro juego de fútbol).

    Se buscan píxeles que no son césped ni líneas blancas en las filas que son campo (no grada)
    y se toma la zona de ancho CROWD_WINDOW con más jugadores: ahí está la jugada. Evita que la
    cámara virtual se quede en zonas vacías del campo."""
    f = frame.astype(np.int16)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    grass = (g * 100 > r * 112) & (g * 100 > b * 122) & (g > 45)
    white = (r > 175) & (g > 175) & (b > 175)
    m = mask[: frame.shape[0], : frame.shape[1]] > 0
    m = m.copy()
    m[int(frame.shape[0] * 0.79):] = False  # franja de abajo: nombres, radar, cartas (HUD)
    rows = (grass & m).mean(axis=1) > 0.45  # filas de campo
    if rows.sum() < frame.shape[0] * 0.2:
        return None
    players = (~grass) & (~white) & m & rows[:, None]
    # solo píxeles con césped cerca (dentro del campo, no carteles ni banquillo)
    col = players.sum(axis=0).astype(np.float32)
    if col.sum() < frame.shape[0] * frame.shape[1] * 0.002:
        return None
    k = max(3, int(frame.shape[1] * CROWD_WINDOW))
    dens = np.convolve(col, np.ones(k, np.float32), mode="same")
    peak = int(dens.argmax())
    lo, hi = max(0, peak - k // 2), min(col.size, peak + k // 2 + 1)
    w = col[lo:hi]
    return float((w * np.arange(lo, hi)).sum() / max(w.sum(), 1e-6)) / col.size  # el triángulo mide ~10 px en 1080p: a 320 px desaparecería


def _blobs(frame: np.ndarray, mask: np.ndarray, color: str) -> list[tuple[float, float]]:
    """Centros (x, y en fracción) de manchas pequeñas y compactas del color dado."""
    import cv2

    f = frame.astype(np.int16)
    m = INDICATOR_COLORS[color](f[..., 0], f[..., 1], f[..., 2]) & (mask > 0)
    n, _, stats, cents = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    h, w = m.shape
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if 6 <= area <= 90 and bw <= 16 and bh <= 16 and area >= 0.3 * bw * bh:
            out.append((cents[i][0] / w, cents[i][1] / h))
    return out


def calibrate_indicator(frames: list[np.ndarray], mask: np.ndarray) -> str | None:
    """Qué color usa el indicador en este clip: el que aparece como UNA mancha pequeña en más muestras."""
    best, best_n = None, 0
    for color in INDICATOR_COLORS:
        n = sum(1 for f in frames if len(_blobs(f, mask, color)) == 1)
        if n > best_n:
            best, best_n = color, n
    return best if best_n >= max(3, len(frames) * 0.15) else None


def indicator_x(frame: np.ndarray, mask: np.ndarray, color: str, near: float | None = None) -> float | None:
    blobs = _blobs(frame, mask, color)
    if not blobs:
        return None
    if near is None or len(blobs) == 1:
        return blobs[0][0] if len(blobs) == 1 else None
    return min(blobs, key=lambda b: abs(b[0] - near))[0]


def track_path(
    src: str | Path,
    start: float,
    dur: float,
    src_w: int,
    src_h: int,
    exclude: list[list[float]] | None = None,
    mode: str = "movimiento",
    fps: float = SAMPLE_FPS,
    progress: Callable[[float], None] | None = None,
) -> list[tuple[float, float]]:
    """[(t relativo a start, x del centro de la acción en fracción 0-1)] sin suavizar."""
    w = IND_WIDTH if mode == "indicador" else WIDTH
    h = max(2, int(round(w * src_h / src_w / 2)) * 2)
    mask = _mask(w, h, exclude or [])
    frames = list(_frames(src, start, dur, w, h, fps))
    color = calibrate_indicator(frames[:: max(1, len(frames) // 40)], mask) if mode == "indicador" else None
    # El movimiento se mide siempre a baja resolución (rápido); el indicador, a 960 px.
    sw = WIDTH
    sh = max(2, int(round(sw * src_h / src_w / 2)) * 2)
    smask = _mask(sw, sh, exclude or [])
    cols = np.arange(sw, dtype=np.float32)
    prev = None
    out: list[tuple[float, float]] = []
    last_x = 0.5
    seen = pending = None  # última x del indicador / salto pendiente de confirmar
    lost = 0  # muestras seguidas sin ver el indicador
    total = max(1, len(frames))
    for i, frame in enumerate(frames):
        t = i / fps
        small = frame if w == sw else frame[:: max(1, w // sw), :: max(1, w // sw)][:sh, :sw]
        gray = small.mean(axis=2).astype(np.float32)
        x = None
        if color:
            x = indicator_x(frame, mask, color, last_x)
            if x is not None and seen is not None and abs(x - seen) > JUMP:
                # Salto grande: puede ser otro objeto del mismo color. Solo se acepta si se repite.
                if pending is not None and abs(x - pending) < JUMP / 2:
                    pending = None
                else:
                    pending, x = x, None
            if x is not None:
                seen, lost = x, 0
            else:
                lost += 1
        # Sin indicador: se queda quieta un momento (suele volver enseguida) antes de seguir el movimiento.
        if x is None and color and lost <= HOLD * fps:
            x = last_x
        if x is None and prev is not None and prev.shape == gray.shape:
            diff = np.abs(gray - prev) * smask[: gray.shape[0], : gray.shape[1]]
            diff[diff < 18] = 0  # ruido de compresión
            col = diff.sum(axis=0)
            energy = col.sum()
            if energy > sw * sh * 0.15:
                # escena entera cambiando (corte, repetición, giro brusco): se mantiene
                x = last_x if energy > sw * sh * 8 else float((col * cols[: col.size]).sum() / energy) / col.size
        if x is not None:
            last_x = x
        out.append((t, last_x))
        prev = gray
        if progress and i % 10 == 0:
            progress(min(1.0, i / total))
    return out


def smooth_path(path: list[tuple[float, float]], crop_frac: float, dead: float = 0.06,
                alpha: float = 0.18, max_speed: float = 0.35) -> list[tuple[float, float]]:
    """Cámara virtual suave: centro del recorte (fracción) en cada muestra.

    - `dead`: no se mueve por cambios pequeños (fracción del ancho);
    - suavizado exponencial hacia delante y hacia atrás (sin retraso);
    - `max_speed`: como mucho esa fracción del ancho por segundo;
    - el centro se limita para que el recorte no se salga del vídeo.
    """
    if not path:
        return []
    half = crop_frac / 2
    lo, hi = half, 1 - half
    xs = np.array([x for _, x in path], np.float64)
    ts = np.array([t for t, _ in path], np.float64)
    # zona muerta
    target = np.empty_like(xs)
    cur = xs[0]
    for i, x in enumerate(xs):
        if abs(x - cur) > dead:
            cur = x - np.sign(x - cur) * dead
        target[i] = cur
    # suavizado en los dos sentidos
    fwd = target.copy()
    for i in range(1, len(fwd)):
        fwd[i] = fwd[i - 1] + alpha * (fwd[i] - fwd[i - 1])
    bwd = fwd.copy()
    for i in range(len(bwd) - 2, -1, -1):
        bwd[i] = bwd[i + 1] + alpha * (bwd[i] - bwd[i + 1])
    # velocidad máxima
    outx = bwd.copy()
    for i in range(1, len(outx)):
        dt = max(ts[i] - ts[i - 1], 1e-3)
        step = np.clip(outx[i] - outx[i - 1], -max_speed * dt, max_speed * dt)
        outx[i] = outx[i - 1] + step
    outx = np.clip(outx, lo, hi)
    return [(float(t), float(x)) for t, x in zip(ts, outx)]


def write_sendcmd(path: list[tuple[float, float]], crop_w: int, src_w: int, dst: Path,
                  rate: float = 30.0, time_map: Callable[[float], float | None] | None = None) -> Path:
    """Archivo sendcmd que mueve `crop@gp` (x en píxeles) interpolando el recorrido a `rate` Hz.

    `time_map` convierte el tiempo del original al de la salida (tras cortar silencios) y devuelve
    None si ese instante se ha cortado."""
    lines = []
    if path:
        ts = np.array([t for t, _ in path])
        xs = np.array([x for _, x in path])
        t = 0.0
        end = ts[-1] + 1.0 / SAMPLE_FPS
        last_px = None
        while t <= end:
            cx = float(np.interp(t, ts, xs))
            px = int(round(cx * src_w - crop_w / 2))
            px = max(0, min(src_w - crop_w, px))
            tt = time_map(t) if time_map else t
            if tt is not None and px != last_px:
                lines.append(f"{tt:.3f} crop@gp x {px};")
                last_px = px
            t += 1.0 / rate
    if not lines:
        lines = ["0.000 crop@gp x 0;"] if crop_w >= src_w else [f"0.000 crop@gp x {(src_w - crop_w) // 2};"]
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dst
