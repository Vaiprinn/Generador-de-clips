import numpy as np

from pipeline import facedetect as F


def test_find_face_prefers_repeated_location():
    dets = [(i, 1650, 180, 90, 110) for i in range(8)]  # la webcam: en todos los fotogramas
    dets += [(2, 1380, 830, 60, 70), (5, 200, 400, 70, 80)]  # caras sueltas (cromos, jugadores)
    face = F.find_face(dets, 12)
    assert face == (1650, 180, 90, 110)


def test_find_face_needs_consistency():
    assert F.find_face([(0, 100, 100, 80, 90)], 12) is None
    assert F.find_face([], 12) is None


def test_refine_finds_webcam_borders():
    # Juego = ruido que cambia en cada fotograma; webcam = zona fija arriba a la derecha.
    rng = np.random.default_rng(0)
    h, w = 200, 320
    cam = rng.integers(20, 60, size=(90, 100))
    frames = []
    for _ in range(8):
        f = rng.integers(0, 255, size=(h, w))
        f[0:90, 220:320] = cam
        frames.append(f)
    l, t, r, b = F.refine_facecam(np.stack(frames).astype(np.uint8), (255, 20, 30, 36))
    assert abs(l - 220) <= 2 and t == 0 and r == 320 and abs(b - 90) <= 2


def test_face_to_facecam_stays_inside():
    rect = F.face_to_facecam((1850, 10, 60, 60), 1920, 1080)
    assert rect[0] + rect[2] <= 1.0 and rect[1] >= 0
