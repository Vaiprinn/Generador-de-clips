"""Prueba de extremo a extremo con FFmpeg real y palabras simuladas (sin Whisper)."""
import json
import shutil
import subprocess

import pytest

from pipeline.job import process_video, render_preview_frame
from pipeline.media import has_amf
from pipeline.presets import load_preset
from pipeline.transcribe import Word

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no instalado")


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    src = d / "partida gta.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3",
        "-f", "lavfi", "-i", "sine=f=440:d=3", "-f", "lavfi", "-i", "sine=f=880:d=3",
        "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest", str(src),
    ], check=True)
    return src


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.parametrize("layout_type,audio,style", [
    ("facecam_esquina", "mix", "frase"), ("sin_facecam", "1", "resaltado"),
    ("split", "0", "karaoke"), ("blur", "0", "frase"),
])
def test_short_render(sample, tmp_path, layout_type, audio, style):
    preset = load_preset("gta")
    preset["layout"]["type"] = layout_type
    preset["subtitles"]["style"] = style
    preset["toggles"]["hook_title"] = True
    preset["hook"]["text"] = "una tragedia"
    preset["audio"]["output_track"] = audio
    preset["render"]["encoder"] = "x264"
    words = [Word(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate("ay madre qué tragedia de partida".split())]
    res = process_video(sample, preset, words=words, output_dir=tmp_path)
    data = _probe(res.video)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next(s for s in data["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert v["r_frame_rate"] == "30/1"
    assert a["sample_rate"] == "48000"
    assert abs(float(data["format"]["duration"]) - 3.0) < 0.2
    names = {p.name for p in res.files}
    assert {"partida_gta_short.mp4", "partida_gta.srt", "partida_gta.ass",
            "partida_gta_words.json", "partida_gta_meta.txt"} <= names
    assert "6. Render (FFmpeg)" in res.timings


@pytest.mark.parametrize("layout_type", ["facecam_esquina", "split"])
def test_short_render_whole_game_fit(sample, tmp_path, layout_type):
    preset = load_preset("eafc27")
    preset["layout"].update(type=layout_type, gameplay_rect=[0, 0, 1, 1], facecam_rect=[0.79, 0, 0.21, 0.32])
    preset["render"]["encoder"] = "x264"
    res = process_video(sample, preset, words=[Word("golazo", 0.2, 0.8)], output_dir=tmp_path)
    v = next(s for s in _probe(res.video)["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)


@pytest.mark.skipif(not has_amf(), reason="sin GPU AMD (h264_amf)")
def test_short_render_amf(sample, tmp_path):
    preset = load_preset("gta")
    preset["render"]["encoder"] = "amf"
    words = [Word(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate("ay madre qué tragedia".split())]
    res = process_video(sample, preset, words=words, output_dir=tmp_path)
    v = next(s for s in _probe(res.video)["streams"] if s["codec_type"] == "video")
    assert (v["codec_name"], v["width"], v["height"]) == ("h264", 1080, 1920)
    assert "h264_amf" in res.summary


def test_long_render(sample, tmp_path):
    preset = load_preset("largo_youtube")
    preset["render"]["encoder"] = "x264"
    preset["toggles"]["hook_title"] = True  # se ignora en Modo Largo
    words = [Word(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate("bienvenidos a un vídeo nuevo".split())]
    res = process_video(sample, preset, words=words, output_dir=tmp_path)
    v = next(s for s in _probe(res.video)["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1280, 720)
    assert res.video.name == "partida_gta_largo.mp4"
    assert (tmp_path / "partida_gta.srt").exists()
    assert "Gancho" not in (tmp_path / "partida_gta.ass").read_text(encoding="utf-8")


def test_reviewed_words_keep_manual_highlights(sample, tmp_path):
    preset = load_preset("gta")
    preset["render"]["encoder"] = "x264"
    words = [Word("hola", 0.1, 0.4, hl=True), Word("franklin", 0.5, 0.9, hl=False)]
    process_video(sample, preset, words=words, output_dir=tmp_path, reviewed=True)
    data = json.loads((tmp_path / "partida_gta_words.json").read_text(encoding="utf-8"))
    assert [d["hl"] for d in data] == [True, False]


def test_render_with_all_phase3_effects(tmp_path):
    src = tmp_path / "talk.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=6",
        "-f", "lavfi", "-i", "aevalsrc='if(lt(t,1),0.2*sin(2*PI*300*t),if(between(t,2.5,4),0.9*sin(2*PI*300*t),0))':s=48000:d=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src),
    ], check=True)
    preset = load_preset("gta")
    preset["render"]["encoder"] = "x264"
    preset["handle"] = "@prueba"
    preset["hook"]["text"] = "una tragedia"
    preset["sfx"]["on_cut"] = "swish.wav"
    for k in ("cut_silences", "auto_zoom", "sfx", "progress_bar", "watermark", "hook_title"):
        preset["toggles"][k] = True
    words = [Word("hola", 0.1, 0.4), Word("chicos", 0.45, 0.9), Word("NOOOO", 2.6, 3.2, hl=True), Word("madre", 3.3, 3.9)]
    res = process_video(src, preset, words=words, output_dir=tmp_path / "out", reviewed=True)
    data = _probe(res.video)
    assert abs(float(data["format"]["duration"]) - 2.95) < 0.15  # 6 s − silencios
    assert res.stats["cortes"] == 1 and res.stats["zooms"] == 1 and res.stats["sfx"] == 3
    ass = (tmp_path / "out" / "talk.ass").read_text(encoding="utf-8")
    assert "@prueba" in ass and "UNA TRAGEDIA" in ass
    remapped = json.loads((tmp_path / "out" / "talk_words.json").read_text(encoding="utf-8"))
    assert remapped[2]["start"] == pytest.approx(1.4, abs=0.01)


def test_preview_frame(sample, tmp_path):
    out = render_preview_frame(sample, load_preset("gta"), 1.0, tmp_path / "p.png")
    assert out.exists() and out.stat().st_size > 1000
