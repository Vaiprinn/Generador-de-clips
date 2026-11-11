"""Zoom elegido a mano, gancho que se desvanece, seguimiento en todos los encuadres."""
from pipeline.job import zoom_moments
from pipeline.layout import HUD_SCORE_RECT, FilterOptions, _zoom_moments, compute_short_layout
from pipeline.subtitles import build_ass


def test_zoom_moments_map_to_short_time():
    z = [{"t": 12.0, "d": 2.0, "x": 0.2, "y": 0.3, "w": 0.5}]
    assert zoom_moments(z, None, (10.0, 30.0)) == [(2.0, 4.0, 0.2, 0.3, 0.5)]
    # con un corte de silencio entre 5 y 11: los tiempos se desplazan
    segs = [(0.0, 5.0), (11.0, 30.0)]
    a, b, *_ = zoom_moments(z, segs, None)[0]
    assert abs(a - 6.0) < 1e-6 and abs(b - 8.0) < 1e-6
    assert zoom_moments([{"t": 2, "d": 1}], [(10.0, 20.0)], None) == []  # cae en un hueco


def test_zoom_filter():
    parts = []
    last = _zoom_moments(parts, "[x]", FilterOptions(fps="60", zooms=[(1.0, 3.0, 0.2, 0.3, 0.5)]), 1080, 1920)
    assert last == "[zm]" and "zoompan" in parts[0] and "1.0000*" in parts[0] and "fps=60" in parts[0]
    assert _zoom_moments([], "[x]", FilterOptions(), 1080, 1920) == "[x]"


def test_hook_fade_out():
    def line(fade):
        ass = build_ass([], {}, hook={"text": "QUÉ GOLAZO", "anim": "pop", "fade_out": fade, "duration": 3}, duration=10)
        return next(l for l in ass.splitlines() if l.startswith("Dialogue: 1"))
    assert "\\fad(0,1200)" in line(1.2)
    assert "\\fad(0,0)" in line(0)
    assert "\\fad(0,600)" in line(None) or "\\fad(0,0)" in line(None)


def test_seguimiento_game_starts_below_scoreboard():
    lay = compute_short_layout(1920, 1080, {"type": "seguimiento", "facecam_rect": [0.79, 0, 0.21, 0.32]})
    assert lay.gameplay.y >= int((HUD_SCORE_RECT[1] + HUD_SCORE_RECT[3]) * 1080)
    off = compute_short_layout(1920, 1080, {"type": "seguimiento", "hud_score": False})
    assert off.gameplay.y < lay.gameplay.y


def test_tracking_in_every_crop_layout():
    for kind in ("sin_facecam", "facecam_esquina", "split", "seguimiento"):
        lay = compute_short_layout(1920, 1080, {"type": kind, "track": "indicador", "facecam_rect": [0.79, 0, 0.21, 0.32]})
        assert lay.track, kind
    assert not compute_short_layout(1920, 1080, {"type": "sin_facecam", "track": "no"}).track
    assert not compute_short_layout(1920, 1080, {"type": "blur", "track": "indicador"}).track
