from pipeline import layout as L


def test_gameplay_crop_16_9_center():
    r = L.gameplay_crop(1920, 1080)
    assert (r.w, r.h) == (608, 1080)
    assert r.x == (1920 - 608) // 2


def test_gameplay_crop_offset_bounds():
    left = L.gameplay_crop(1920, 1080, offset=-1)
    right = L.gameplay_crop(1920, 1080, offset=1)
    assert left.x == 0
    assert right.x + right.w == 1920


def test_gameplay_crop_zoom_and_even():
    r = L.gameplay_crop(2560, 1440, zoom=1.37)
    assert r.w % 2 == 0 and r.h % 2 == 0
    assert r.x + r.w <= 2560 and r.y + r.h <= 1440


def test_gameplay_crop_vertical_source():
    r = L.gameplay_crop(1080, 1920)
    assert (r.w, r.h) == (1080, 1920)


def test_facecam_dest_positions():
    src = L.Rect(0, 0, 480, 270)
    cfg = {"facecam_width": 0.6, "facecam_y": 200, "facecam_margin": 40}
    tr = L.facecam_dest_rect(src, {**cfg, "facecam_position": "top-right"})
    tl = L.facecam_dest_rect(src, {**cfg, "facecam_position": "top-left"})
    tc = L.facecam_dest_rect(src, {**cfg, "facecam_position": "top-center"})
    assert tr.w == 648 and tr.y == 200
    assert tr.x + tr.w == 1080 - 40
    assert tl.x == 40
    assert tc.x == (1080 - 648) // 2


def test_facecam_rect_clamped_inside_source():
    r = L.facecam_source_rect(1920, 1080, [0.9, 0.9, 0.3, 0.3])
    assert r.x + r.w <= 1920 and r.y + r.h <= 1080


def test_escape_filter_path_windows_style():
    s = L.escape_filter_path("subs.ass")
    assert s.startswith("'") and s.endswith("'")
    assert "\\" not in s.replace("\\:", "").replace("\\'", "")


def test_filter_graph_facecam(tmp_path):
    lay = L.compute_short_layout(1920, 1080, {"type": "facecam_esquina", "facecam_rect": [0, 0, 0.25, 0.25]})
    fg = L.build_short_filter(lay, {"facecam_shadow": True}, tmp_path, ass_path=tmp_path / "a.ass")
    assert "alphamerge" in fg.graph and "ass=filename=" in fg.graph
    assert len(fg.extra_inputs) == 2
    assert (tmp_path / "facecam_mask.png").exists()


def test_filter_graph_no_facecam(tmp_path):
    lay = L.compute_short_layout(1920, 1080, {"type": "sin_facecam"})
    fg = L.build_short_filter(lay, {}, tmp_path)
    assert "split" not in fg.graph and fg.extra_inputs == []


def test_split_layout_geometry():
    lay = L.compute_short_layout(1920, 1080, {"type": "split", "split_ratio": 0.4, "facecam_rect": [0, 0, 0.25, 0.25]})
    assert lay.facecam_dst.y == 0 and lay.facecam_dst.w == 1080
    assert lay.gameplay_dst.y == lay.facecam_dst.h and lay.gameplay_dst.bottom == 1920
    # recortes con la misma relación de aspecto que su hueco
    assert abs(lay.facecam_src.w / lay.facecam_src.h - 1080 / lay.facecam_dst.h) < 0.02
    assert abs(lay.gameplay.w / lay.gameplay.h - 1080 / lay.gameplay_dst.h) < 0.02


def test_blur_layout_geometry(tmp_path):
    lay = L.compute_short_layout(1920, 1080, {"type": "blur", "blur_fg_y": 960})
    assert lay.gameplay_dst.w == 1080 and lay.gameplay_dst.h == 608
    assert lay.gameplay_dst.y == 960 - 304
    fg = L.build_short_filter(lay, {"blur_strength": 20}, tmp_path)
    assert "boxblur" in fg.graph and fg.extra_inputs == []


def test_gameplay_from_rect_roundtrip():
    # Marcar exactamente el recorte que daría offset 0.5 / zoom 1.5
    target = L.gameplay_crop(1920, 1080, offset=0.5, zoom=1.5)
    frac = [target.x / 1920, target.y / 1080, target.w / 1920, target.h / 1080]
    ox, oy, zoom = L.gameplay_from_rect(1920, 1080, frac)
    assert abs(zoom - 1.5) < 0.02 and abs(ox - 0.5) < 0.03 and abs(oy) < 0.03


def test_gameplay_rect_whole_frame_fits_with_blur(tmp_path):
    cfg = {"type": "facecam_esquina", "gameplay_rect": [0, 0, 1, 1], "facecam_rect": [0.79, 0, 0.21, 0.32]}
    lay = L.compute_short_layout(1920, 1080, cfg)
    assert lay.fit and (lay.gameplay.w, lay.gameplay.h) == (1920, 1080)
    fg = L.build_short_filter(lay, {}, tmp_path)
    assert "[gpbg][gpfg]overlay" in fg.graph and "boxblur" in fg.graph


def test_gameplay_rect_with_screen_shape_fills():
    # Recuadro 9:16 marcado a mano: llena la pantalla sin bordes
    frac = [0.3, 0, 608 / 1920, 1.0]
    lay = L.compute_short_layout(1920, 1080, {"type": "sin_facecam", "gameplay_rect": frac})
    assert not lay.fit
    assert abs(lay.gameplay.x - 0.3 * 1920) <= 2 and abs(lay.gameplay.w / lay.gameplay.h - 1080 / 1920) < 0.01


def test_split_gameplay_rect_uses_bottom_aspect():
    cfg = {"type": "split", "split_ratio": 0.42, "gameplay_rect": [0, 0, 1, 1]}
    assert abs(L.gameplay_dst_aspect(1920, 1080, cfg) - 1080 / (1920 - 806)) < 0.01
    assert L.compute_short_layout(1920, 1080, cfg).fit


def test_hook_y_below_facecam():
    lay = L.compute_short_layout(1920, 1080, {"type": "facecam_esquina", "facecam_y": 200, "facecam_width": 0.6})
    assert lay.facecam_dst.bottom < lay.hook_y < 1100


def test_long_filter_sizes():
    fg, size = L.build_long_filter(2560, 1440, "1080p")
    assert size == (1920, 1080) and "scale=1920:1080" in fg.graph
    fg, size = L.build_long_filter(1920, 1080, "original")
    assert size == (1920, 1080) and "scale" not in fg.graph
