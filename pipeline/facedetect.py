"""Botón "autodetectar facecam": busca la cara en varios fotogramas y propone el recuadro.

Usa MediaPipe si está instalado y, si no, el detector Haar de OpenCV. Ambos son
opcionales (requirements-extras.txt). Devuelve [x, y, ancho, alto] en fracciones.
"""
from __future__ import annotations

import statistics
import tempfile
from pathlib import Path

from . import media
from .paths import WORK_DIR

FACECAM_ASPECT = 4 / 3  # recuadro típico de cámara (cabeza y hombros)
FACE_TO_CAM_WIDTH = 3.0  # el recuadro mide ~3 veces el ancho de la cara


class DetectorUnavailable(RuntimeError):
    pass


def _detector():
    """Devuelve una función imagen_rgb(numpy) → lista de (x, y, w, h) en píxeles."""
    try:
        import mediapipe as mp

        solutions = getattr(mp, "solutions", None)
        if solutions is not None and hasattr(solutions, "face_detection"):
            fd = solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)

            def detect_mp(img):
                h, w = img.shape[:2]
                res = fd.process(img)
                out = []
                for d in res.detections or []:
                    b = d.location_data.relative_bounding_box
                    out.append((b.xmin * w, b.ymin * h, b.width * w, b.height * h))
                return out

            return detect_mp
    except ImportError:
        pass
    try:
        import cv2

        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if cascade.empty():
            raise DetectorUnavailable("Tu OpenCV no incluye los modelos Haar: instala opencv-python<5")

        def detect_cv(img):
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(32, 32))
            return [tuple(map(float, f)) for f in faces]

        return detect_cv
    except ImportError:
        pass
    raise DetectorUnavailable(
        "Instala los extras para autodetectar: .venv\\Scripts\\python -m pip install mediapipe opencv-python"
    )


def face_to_facecam(face: tuple[float, float, float, float], src_w: int, src_h: int) -> list[float]:
    fx, fy, fw, fh = face
    w = min(fw * FACE_TO_CAM_WIDTH, src_w)
    h = min(w / FACECAM_ASPECT, src_h)
    cx = fx + fw / 2
    cy = fy + fh / 2 + 0.12 * h  # la cara suele estar algo por encima del centro
    x = max(0.0, min(cx - w / 2, src_w - w))
    y = max(0.0, min(cy - h / 2, src_h - h))
    return [round(x / src_w, 4), round(y / src_h, 4), round(w / src_w, 4), round(h / src_h, 4)]


def detect_facecam(src: str | Path, samples: int = 6) -> list[float] | None:
    """Analiza `samples` fotogramas repartidos por el vídeo. None si no ve ninguna cara."""
    import numpy as np
    from PIL import Image

    detect = _detector()
    info = media.probe(src)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    faces = []
    with tempfile.TemporaryDirectory(dir=WORK_DIR) as tmp:
        for i in range(samples):
            t = info.duration * (i + 0.5) / samples
            png = media.extract_frame(src, Path(tmp) / f"f{i}.png", t)
            img = np.asarray(Image.open(png).convert("RGB"))
            found = detect(img)
            if found:
                faces.append(max(found, key=lambda f: f[2] * f[3]))  # la más grande
    if not faces:
        return None
    median = tuple(statistics.median(f[k] for f in faces) for k in range(4))
    return face_to_facecam(median, info.width, info.height)
