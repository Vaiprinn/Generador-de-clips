"""Botón "autodetectar facecam": encuentra el recuadro de la webcam en el vídeo.

1. Busca tu cara con YuNet (detector DNN de OpenCV, modelo de 230 KB en models/) en
   varios fotogramas y se queda con la que aparece siempre en el mismo sitio (las caras
   de jugadores, cromos o miniaturas salen pequeñas o en sitios distintos).
2. Ajusta el recuadro a los bordes reales de la webcam: líneas rectas que no cambian
   entre fotogramas o, si no las hay, el punto donde empieza el juego (que se mueve
   mucho más que la webcam). Si tampoco, usa una estimación a partir de la cara.

Devuelve [x, y, ancho, alto] en fracciones del vídeo original.
"""
from __future__ import annotations

import subprocess
import urllib.request
from pathlib import Path

import numpy as np

from . import media
from .paths import MODELS_DIR, ffmpeg_bin

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
YUNET_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
             "face_detection_yunet/" + YUNET_FILE)

FACECAM_ASPECT = 4 / 3  # recuadro típico de cámara (cabeza y hombros)
FACE_TO_CAM_WIDTH = 3.0  # el recuadro mide ~3 veces el ancho de la cara
MIN_SCORE = 0.7
MIN_FACE_H = 0.05  # cara mínima: 5 % del alto del vídeo
LINE_COVERAGE = 0.6  # un borde cuenta si cubre el 60 % del lado
SAMPLES = 12


class DetectorUnavailable(RuntimeError):
    pass


def _model_path() -> Path:
    path = MODELS_DIR / YUNET_FILE
    if not path.exists():
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            req = urllib.request.Request(YUNET_URL, headers={"User-Agent": "ClipForge"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            if len(data) < 100_000:
                raise ValueError("descarga incompleta")
            path.write_bytes(data)
        except Exception as e:
            raise DetectorUnavailable(f"No se pudo descargar el modelo de caras ({e}). "
                                      f"Descárgalo a mano en models/{YUNET_FILE}") from e
    return path


def _cv2():
    try:
        import cv2
    except ImportError as e:
        raise DetectorUnavailable(
            "Falta OpenCV: .venv\\Scripts\\python -m pip install opencv-python-headless"
        ) from e
    return cv2


def _frames(src: str | Path, w: int, h: int, duration: float, n: int) -> list[np.ndarray]:
    """`n` fotogramas BGR repartidos por el vídeo (sin pasar por disco)."""
    out = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        raw = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(src),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
            capture_output=True, creationflags=media._NO_WINDOW,
        ).stdout
        if len(raw) == w * h * 3:
            out.append(np.frombuffer(raw, np.uint8).reshape(h, w, 3))
    return out


def find_face(detections: list[tuple[int, float, float, float, float]], n_frames: int):
    """La cara que se repite en el mismo sitio en más fotogramas. detections: (frame, x, y, w, h)."""
    clusters: list[list] = []
    for d in detections:
        cx, cy = d[1] + d[3] / 2, d[2] + d[4] / 2
        for c in clusters:
            m = np.median([[e[1] + e[3] / 2, e[2] + e[4] / 2, e[3]] for e in c], axis=0)
            if abs(cx - m[0]) < 0.6 * m[2] and abs(cy - m[1]) < 0.6 * m[2]:
                c.append(d)
                break
        else:
            clusters.append([d])
    if not clusters:
        return None
    best = max(clusters, key=lambda c: (len({e[0] for e in c}), np.mean([e[3] * e[4] for e in c])))
    if len({e[0] for e in best}) < max(2, 0.3 * n_frames):
        return None
    return tuple(float(v) for v in np.median([e[1:] for e in best], axis=0))


def face_to_facecam(face: tuple[float, float, float, float], src_w: int, src_h: int) -> list[float]:
    fx, fy, fw, fh = face
    w = min(fw * FACE_TO_CAM_WIDTH, src_w)
    h = min(w / FACECAM_ASPECT, src_h)
    cx = fx + fw / 2
    cy = fy + fh / 2 + 0.12 * h  # la cara suele estar algo por encima del centro
    x = max(0.0, min(cx - w / 2, src_w - w))
    y = max(0.0, min(cy - h / 2, src_h - h))
    return [round(x / src_w, 4), round(y / src_h, 4), round(w / src_w, 4), round(h / src_h, 4)]


def _line(edge: np.ndarray, positions: np.ndarray, span: tuple[int, int], vertical: bool, thr: float):
    """Mejor borde recto entre `positions`: (posición, cobertura 0-1)."""
    if len(positions) == 0:
        return None, 0.0
    a, b = span
    seg = edge[a:b][:, positions] if vertical else edge[positions][:, a:b].T
    cover = (seg > thr).mean(axis=0)
    i = int(np.argmax(cover))
    return int(positions[i]), float(cover[i])


def _motion_step(profile: np.ndarray, start: int, direction: int, edge: int, run: int = 4) -> int:
    """Saliendo de la cara, primer punto donde la variación entre fotogramas se dispara (empieza el juego)."""
    n = len(profile)
    start = int(np.clip(start, 0, n - 1))
    inner = profile[max(0, start - 10):start + 1] if direction > 0 else profile[start:start + 11]
    base = float(np.median(inner)) if len(inner) else float(np.median(profile))
    thr = max(2.5 * base, base + 6)
    i = start
    while 0 <= i < n:
        seg = profile[i:i + run] if direction > 0 else profile[max(0, i - run + 1):i + 1]
        if len(seg) == run and np.all(seg > thr):
            return i if direction > 0 else i + 1
        i += direction
    return edge


def refine_facecam(gray: np.ndarray, face: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """Ajusta el recuadro a los bordes de la webcam. gray: (n, h, w) fotogramas en gris. Devuelve l, t, r, b."""
    _, H, W = gray.shape
    g = gray.astype(np.float32)
    gx = np.pad(np.median(np.abs(np.diff(g, axis=2)), axis=0), ((0, 0), (0, 1)))
    gy = np.pad(np.median(np.abs(np.diff(g, axis=1)), axis=0), ((0, 1), (0, 0)))
    std = g.std(axis=0)
    thr = max(8.0, float(np.percentile(np.concatenate([gx.ravel(), gy.ravel()]), 90)))

    fx, fy, fw, fh = face
    rows = (int(max(0, fy - 0.2 * fh)), int(min(H, fy + 1.2 * fh)))
    cols = (int(max(0, fx + fw / 2 - fw)), int(min(W, fx + fw / 2 + fw)))
    row_motion, col_motion = std[rows[0]:rows[1]].mean(axis=0), std[:, cols[0]:cols[1]].mean(axis=1)

    def side(positions, span, vertical, motion, start, direction, edge, after=0):
        pos, cover = _line(gx if vertical else gy, positions, span, vertical, thr)
        if cover >= LINE_COVERAGE:
            return pos + after
        return _motion_step(motion, start, direction, edge)

    ar = np.arange
    left = side(ar(int(max(1, fx - 3.5 * fw)), int(max(2, fx - 0.15 * fw))), rows, True,
                row_motion, int(fx), -1, 0)
    right = side(ar(int(min(W - 2, fx + 1.15 * fw)), int(min(W - 1, fx + 4.5 * fw))), rows, True,
                 row_motion, int(fx + fw), 1, W, after=1)
    top = side(ar(int(max(1, fy - 3.0 * fh)), int(max(2, fy - 0.1 * fh))), cols, False,
               col_motion, int(fy), -1, 0)
    bottom = side(ar(int(min(H - 2, fy + 1.05 * fh)), int(min(H - 1, fy + 5.0 * fh))), cols, False,
                  col_motion, int(fy + fh), 1, H, after=1)
    return left, top, right, bottom


def _plausible(rect: tuple[int, int, int, int], face, W: int, H: int) -> bool:
    l, t, r, b = rect
    fx, fy, fw, fh = face
    w, h = r - l, b - t
    return (fw * 1.3 <= w <= 0.7 * W and fh * 1.3 <= h <= 0.8 * H and 0.5 <= w / max(h, 1) <= 2.5
            and l <= fx and r >= fx + fw and t <= fy and b >= fy + fh)


def content_box(gray: np.ndarray, rect: tuple[int, int, int, int], inset: int = 2,
                max_trim: float = 0.15) -> tuple[int, int, int, int]:
    """Dentro del recuadro de la webcam, quita las bandas negras de la propia cámara (cuando su
    imagen no llena la fuente de OBS) y un par de píxeles de borde, que suelen mezclarse con el juego.
    gray: (n, h, w). Devuelve l, t, r, b."""
    l, t, r, b = rect
    g = gray[:, t:b, l:r].astype(np.float32)
    if g.size == 0:
        return rect
    h, w = g.shape[1:]

    def dark(line: np.ndarray) -> bool:  # línea (fotogramas × píxeles) casi negra y lisa
        return float(line.mean()) < 6 and float(np.percentile(line, 99.5)) < 14

    cut = {"l": 0, "t": 0, "r": 0, "b": 0}
    while cut["l"] < max_trim * w and dark(g[:, :, cut["l"]]):
        cut["l"] += 1
    while cut["r"] < max_trim * w and dark(g[:, :, w - 1 - cut["r"]]):
        cut["r"] += 1
    while cut["t"] < max_trim * h and dark(g[:, cut["t"], :]):
        cut["t"] += 1
    while cut["b"] < max_trim * h and dark(g[:, h - 1 - cut["b"], :]):
        cut["b"] += 1
    return (l + cut["l"] + inset, t + cut["t"] + inset, r - cut["r"] - inset, b - cut["b"] - inset)


def detect_facecam(src: str | Path, samples: int = SAMPLES, inner: bool = False):
    """Analiza `samples` fotogramas repartidos por el vídeo. None si no ve ninguna cara.
    Con `inner=True` devuelve (recuadro, contenido): el recuadro entero (para taparlo en el juego) y
    la parte con imagen real de la cámara, sin bandas negras ni borde (para mostrarla)."""
    cv2 = _cv2()
    info = media.probe(src)
    W, H = info.width, info.height
    imgs = _frames(src, W, H, info.duration, samples)
    if not imgs:
        return None
    det = cv2.FaceDetectorYN.create(str(_model_path()), "", (W, H), 0.6, 0.3, 50)
    found = []
    for i, img in enumerate(imgs):
        _, faces = det.detect(img)
        for f in [] if faces is None else faces:
            if f[14] >= MIN_SCORE and f[3] >= MIN_FACE_H * H:
                found.append((i, float(f[0]), float(f[1]), float(f[2]), float(f[3])))
    face = find_face(found, len(imgs))
    if face is None:
        return (None, None) if inner else None
    # El ajuste fino se hace a media resolución (más rápido; el error es de ±2 px).
    s = 2 if W >= 1280 else 1
    gray = np.stack([cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)[::s, ::s] for im in imgs])
    l, t, r, b = (v * s for v in refine_facecam(gray, tuple(v / s for v in face)))
    r, b = min(r, W), min(b, H)
    if not _plausible((l, t, r, b), face, W, H):
        rect = face_to_facecam(face, W, H)
        return (rect, rect) if inner else rect
    rect = [round(l / W, 4), round(t / H, 4), round((r - l) / W, 4), round((b - t) / H, 4)]
    if not inner:
        return rect
    il, it, ir, ib = (v * s for v in content_box(gray, (l // s, t // s, r // s, b // s), inset=max(2, 6 // s)))
    if ir - il < 0.7 * (r - l) or ib - it < 0.7 * (b - t):  # algo raro: mejor el recuadro entero
        return rect, rect
    return rect, [round(il / W, 4), round(it / H, 4), round((ir - il) / W, 4), round((ib - it) / H, 4)]
