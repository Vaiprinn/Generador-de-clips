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
