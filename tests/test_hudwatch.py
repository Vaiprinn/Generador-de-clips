"""Marcador que desaparece (pausas, menús, final) y recorte limpio de la webcam."""
import numpy as np

from pipeline import hudwatch
from pipeline.facedetect import content_box
from pipeline.layout import FilterOptions, Rect, ShortLayout, build_short_filter, compute_short_layout


def _clip(present: list[bool]) -> np.ndarray:
    rng = np.random.default_rng(1)
    board = rng.integers(0, 255, (26, 64)).astype(np.int16)
    frames = []
    for on in present:
        if on:
            f = board.copy()
            f[18:, 40:] = rng.integers(0, 255, (8, 24))  # el reloj cambia
        else:
            f = rng.integers(0, 255, (26, 64)).astype(np.int16)  # menú / jugada: otra cosa
        frames.append(f)
    return np.stack(frames)


def test_presence_detects_when_scoreboard_goes_away():
    present = [True] * 30 + [False] * 10
    assert hudwatch.presence_from_samples(_clip(present)) == present


def test_presence_ignores_single_glitch():
    present = [True] * 15 + [False] + [True] * 15
    assert all(hudwatch.presence_from_samples(_clip(present)))


def test_enable_expr():
    fps = hudwatch.FPS
    samples = [((i + 0.5) / fps, i < 20) for i in range(40)]
    expr = hudwatch.enable_expr(samples)
    assert expr == "between(t,0.000,5.000)"
    assert hudwatch.enable_expr([(0.1, True), (0.3, True)]) is None
    assert hudwatch.enable_expr([(0.1, False), (0.3, False)]) == "0"
    # con cortes: lo que cae en un hueco no cuenta y el resto se desplaza
    shifted = hudwatch.enable_expr(samples, lambda t: t - 1 if t >= 1 else None)
    assert shifted.startswith("between(t,0.000,4.000)")


def test_hud_overlay_gets_enable():
    lay = ShortLayout("seguimiento", Rect(0, 0, 960, 1080), Rect(0, 500, 1080, 1000),
                      hud=[(Rect(90, 50, 200, 80), Rect(40, 100, 280, 112))])
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        fg = build_short_filter(lay, {}, Path(d), opts=FilterOptions(hud_enable=["between(t,0.000,5.000)"]))
    assert "enable='between(t,0.000,5.000)'" in fg.graph


def test_content_box_trims_black_band_and_edge():
    g = np.full((4, 200, 300), 90, np.uint8)
    g[:, 170:200, :] = 0  # banda negra abajo (la cámara no llena la fuente)
    l, t, r, b = content_box(g, (0, 0, 300, 200), inset=3)
    assert (l, t, r) == (3, 3, 297) and b == 167


def test_layout_shows_inner_but_hides_outer():
    cfg = {"type": "seguimiento", "facecam_rect": [0.79, 0.0, 0.21, 0.32], "facecam_inner": [0.8, 0.01, 0.19, 0.28]}
    lay = compute_short_layout(1920, 1080, cfg)
    assert lay.facecam_src.x >= int(0.8 * 1920) and lay.facecam_src.bottom <= int(0.29 * 1080) + 2
    assert any(h.x <= int(0.79 * 1920) for h in lay.hide)


def test_refine_finds_bottom_close_below_a_low_face():
    """Cara baja dentro de la cámara (apoyado en la mano): el borde de abajo está a 1,2 caras."""
    from pipeline.facedetect import refine_facecam

    rng = np.random.default_rng(3)
    g = rng.integers(0, 255, (6, 400, 600)).astype(np.uint8)  # juego que cambia
    g[:, 100:230, 0:200] = 60  # webcam fija
    l, t, r, b = refine_facecam(g, (60.0, 120.0, 60.0, 95.0))
    assert abs(b - 230) <= 2 and abs(r - 200) <= 2 and abs(t - 100) <= 2
