"""Layouts con cámara arriba (blur_cam, seguimiento), HUD recolocado y seguimiento de la acción."""
import subprocess

import numpy as np
import pytest

from pipeline import layout as L
from pipeline import tracking as tr
from pipeline.paths import ffmpeg_bin, ffprobe_bin

CAM = [0.79, 0.0, 0.21, 0.32]


def test_seguimiento_geometry():
    lay = L.compute_short_layout(1920, 1080, {"type": "seguimiento", "facecam_rect": CAM, "track": "movimiento"})
    cam, gp, dst = lay.facecam_dst, lay.gameplay, lay.gameplay_dst
    assert lay.track and lay.background is not None
    assert cam.y < 200 and cam.w <= 0.62 * 1080  # cámara más contenida
    assert len(lay.hud) == 1  # solo el marcador: el radar no se recoloca
    score = lay.hud[0][1]
    assert score.x + score.w == cam.x and score.y >= cam.y and score.bottom <= cam.bottom  # pegado a su izquierda
    assert gp.w >= 0.45 * 1920  # plano abierto: se ve la jugada
    assert gp.y + gp.h <= 0.80 * 1080  # la franja del radar queda fuera del recorte
    assert dst.y >= cam.bottom and dst.bottom <= 1920 - 250
    assert len(lay.hide) == 2  # webcam y marcador originales tapados en el juego
    assert dst.y < lay.subs_y < dst.bottom
    wide = L.compute_short_layout(1920, 1080, {"type": "seguimiento", "facecam_rect": CAM, "gameplay_zoom": 0.6})
    assert wide.gameplay.w > gp.w


def test_blur_cam_geometry_and_no_cam():
    lay = L.compute_short_layout(1920, 1080, {"type": "blur_cam", "facecam_rect": CAM})
    assert lay.background is not None and lay.gameplay_dst.y > lay.facecam_dst.bottom
    assert lay.hud == [] and len(lay.hide) == 1
    none = L.compute_short_layout(1920, 1080, {"type": "seguimiento", "facecam_rect": []})
    assert none.facecam_dst is None and none.hook_y > 0


def test_smooth_path_limits_speed_and_bounds():
    path = [(i / 6, 0.2 if i < 30 else 0.9) for i in range(60)]
    s = tr.smooth_path(path, crop_frac=0.32, max_speed=0.3)
    xs = [x for _, x in s]
    assert min(xs) >= 0.16 - 1e-9 and max(xs) <= 0.84 + 1e-9
    assert max(abs(b - a) for a, b in zip(xs, xs[1:])) <= 0.3 / 6 + 1e-6


def test_indicator_calibration_finds_magenta():
    frames = []
    for i in range(10):
        f = np.zeros((540, 960, 3), np.uint8)
        f[..., 1] = 140  # césped
        x = 100 + i * 60
        f[200:206, x:x + 6] = (220, 67, 139)  # triángulo magenta
        frames.append(f)
    mask = np.ones((540, 960), np.float32)
    assert tr.calibrate_indicator(frames, mask) == "magenta"
    assert abs(tr.indicator_x(frames[3], mask, "magenta") - (283 / 960)) < 0.01


def test_render_seguimiento_with_tracking(tmp_path):
    try:
        ffmpeg_bin()
    except FileNotFoundError:
        pytest.skip("ffmpeg no disponible")
    from pipeline.job import process_video
    from pipeline.presets import load_preset

    src = tmp_path / "fc.mp4"
    # un cuadrado blanco que cruza de izquierda a derecha: la cámara virtual tiene que seguirlo
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=green:s=1280x720:r=30:d=6",
                    "-f", "lavfi", "-i", "color=c=white:s=80x80:r=30:d=6", "-f", "lavfi", "-i", "sine=d=6",
                    "-filter_complex", "[0][1]overlay=x='50+t*190':y=320[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    p = load_preset("gta")
    p["layout"].update(type="seguimiento", facecam_rect=[0.0, 0.0, 0.2, 0.2], track="movimiento")
    p["toggles"]["export_srt"] = False
    res = process_video(src, p, words=[], output_dir=tmp_path, stem="seg", hook_text="PRUEBA")
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                          str(res.video)], capture_output=True, text=True, check=True).stdout
    assert "1080,1920" in out
    assert "Seguimiento" in res.summary or "seguimiento" in res.summary
