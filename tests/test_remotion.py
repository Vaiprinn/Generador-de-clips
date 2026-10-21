import json
import os
import shutil
import subprocess

import pytest

from pipeline import remotion_render as rr
from pipeline.layout import compute_short_layout
from pipeline.media import MediaInfo
from fractions import Fraction
from pipeline.transcribe import Word

INFO = MediaInfo("in.mp4", 1920, 1080, Fraction(30), 6.0, [{"index": 0}, {"index": 1}])


def _props(**kw):
    layout = compute_short_layout(1920, 1080, {"type": "facecam_esquina"})
    words = [Word("hola,", 0.1, 0.4), Word("chicos", 0.45, 0.9, hl=True), Word("vamos", 2.0, 2.3)]
    args = dict(
        base=INFO, layout=layout, layout_cfg={"facecam_radius": 30}, duration=3.0, words=words,
        sub_cfg={"style": "karaoke", "case": "mayusculas", "max_words": 3, "animation": "slide"},
        show_subtitles=True, highlight=True,
        hook={"text": "una|tragedia", "pos_y": 700, "duration": 2}, watermark={"text": "@yo", "position": "arriba-der"},
        progress_bar={"color": "#FF0000", "height": 8, "position": "abajo"},
        zoom_times=[1.0], zoom_amount=1.2, fonts=[],
    )
    args.update(kw)
    return rr.build_props(**args)


def test_props_shape_and_formatting():
    p = _props()
    assert (p["width"], p["height"], p["fps"], p["durationInFrames"]) == (1080, 1920, 30.0, 90)
    assert p["layout"]["facecamDst"]["w"] == 648 and p["layout"]["radius"] == 30
    phrases = p["subtitles"]["phrases"]
    assert [w["text"] for w in phrases[0]["words"]] == ["HOLA", "CHICOS"]  # mayúsculas y sin coma
    assert phrases[0]["words"][1]["hl"] is True
    assert p["subtitles"]["animation"] == "slide" and p["subtitles"]["x"] == 510
    assert p["hook"]["text"] == "UNA\nTRAGEDIA" and p["hook"]["y"] == 700
    assert p["watermark"]["align"] == "right"
    assert p["zoom"] == {"times": [1.0], "amount": 1.2}
    json.dumps(p)  # serializable


def test_props_optional_parts_off():
    p = _props(show_subtitles=False, hook=None, watermark=None, progress_bar=None, zoom_times=[])
    assert p["subtitles"] is None and p["hook"] is None and p["watermark"] is None
    assert p["progressBar"] is None and p["zoom"] is None


def test_base_command_copies_video_without_cuts(tmp_path):
    cmd = rr.build_base_command(INFO, tmp_path / "b.mp4", "1", None, None, 0.7)
    assert cmd[cmd.index("-c:v") + 1] == "copy" and "0:a:1" in cmd


def test_base_command_reencodes_with_cuts(tmp_path):
    cmd = rr.build_base_command(INFO, tmp_path / "b.mp4", "mix", "between(t,0,1)", None, 0.7, encoder="x264")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "select='between(t,0,1)'" in graph and "aselect" in graph and "amix" in graph
    assert "libx264" in cmd


@pytest.mark.skipif(
    os.environ.get("CLIPFORGE_TEST_REMOTION") != "1" or not rr.status()[0] or shutil.which("ffmpeg") is None,
    reason="render real con Remotion: activa CLIPFORGE_TEST_REMOTION=1 (tarda ~30 s)",
)
def test_real_remotion_render(tmp_path):
    from pipeline.job import process_video
    from pipeline.presets import load_preset

    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    preset = load_preset("gta")
    preset["render"]["engine"] = "remotion"
    preset["toggles"]["hook_title"] = True
    preset["hook"]["text"] = "prueba"
    res = process_video(src, preset, words=[Word("hola", 0.1, 0.5), Word("mundo", 0.6, 1.0)],
                        output_dir=tmp_path / "out", reviewed=True)
    assert "Remotion" in res.summary
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                          str(res.video)], capture_output=True, text=True).stdout
    assert "1080,1920" in out


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no instalado")
def test_falls_back_to_ffmpeg_when_remotion_missing(tmp_path, monkeypatch):
    from pipeline.job import process_video
    from pipeline.presets import load_preset

    monkeypatch.setattr(rr, "status", lambda: (False, "Falta Node.js"))
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=1",
                    "-f", "lavfi", "-i", "sine=f=440:d=1", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    preset = load_preset("gta")
    preset["render"].update(engine="remotion", encoder="x264")
    res = process_video(src, preset, words=[Word("hola", 0.1, 0.5)], output_dir=tmp_path / "out", reviewed=True)
    assert res.video.exists() and "Remotion no disponible" in res.summary and "libx264" in res.summary
