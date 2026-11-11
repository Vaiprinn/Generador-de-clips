"""Episodio de YouTube: elegir capítulos, textos, miniatura y render real (sin Whisper)."""
import json
import subprocess

import pytest
from PIL import Image

from pipeline import episode as ep
from pipeline import highlights as hl
from pipeline.paths import ffmpeg_bin, ffprobe_bin
from pipeline.presets import load_preset
from pipeline.transcribe import Word


def _have_ffmpeg() -> bool:
    try:
        ffmpeg_bin()
        return True
    except FileNotFoundError:
        return False


def test_pick_chapters_respects_budget_and_order():
    wins = [hl.Window(t, t + 30, score=s) for t, s in
            [(600, 9), (630, 8), (3000, 7), (5000, 6), (100, 1), (7000, 0.5)]]
    cfg = dict(ep.DEFAULTS, target_min=4, pad=60)
    ch = ep.pick_chapters(wins, 8000, cfg)
    assert [c.start for c in ch] == sorted(c.start for c in ch)
    assert ch[0].start == 540 and ch[0].end == 720  # 600 y 630 se funden en uno
    assert sum(c.duration for c in ch) >= 4 * 60
    assert all(c.duration <= cfg["max_chapter"] for c in ch)


def test_chapter_lines_and_description():
    plan = ep.EpisodePlan("x.mp4", 100, [], title="Mi vídeo", description="Hola.", tags=["gta", "fail"])
    starts = [(0, "Intro"), (12.4, "El jefe"), (3725, "Final")]
    assert ep.chapter_lines(starts) == "00:00 Intro\n00:12 El jefe\n1:02:05 Final"
    d = ep.description_text(plan, starts, "@yo")
    assert d.startswith("Mi vídeo") and "00:00 Intro" in d and "Sígueme: @yo" in d and "gta, fail" in d


def test_plan_roundtrip(tmp_path):
    plan = ep.EpisodePlan("x.mp4", 100, [ep.Chapter(1, 50, "A"), ep.Chapter(60, 90, "B", keep=False)],
                          intro=[10, 20], title="T")
    plan.save(tmp_path)
    back, words = ep.load_plan(tmp_path)
    assert back.chapters[1].keep is False and back.intro == [10, 20] and words == []


@pytest.fixture(scope="module")
def vod(tmp_path_factory):
    if not _have_ffmpeg():
        pytest.skip("ffmpeg no disponible")
    d = tmp_path_factory.mktemp("vod")
    src = d / "directo gta.mp4"
    mic = ("anoisesrc=d=300:c=pink:a=0.01,"
           "volume='if(between(t,100,108)+between(t,230,236),40,if(between(t,150,160),0.001,1))':eval=frame")
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=300",
                    "-f", "lavfi", "-i", mic, "-map", "0", "-map", "1", "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "60", "-c:a", "aac", "-shortest", str(src)], check=True)
    return src


def test_thumbnail(vod, tmp_path):
    out = ep.make_thumbnail(vod, 50, "ESTE JEFE ME ODIA", tmp_path / "m.png", {"outline_color": "#E3262E"}, "@yo")
    img = Image.open(out)
    assert img.size == (1280, 720)


def test_plan_and_render(vod, tmp_path, monkeypatch):
    talk = [Word("hola", t, t + 0.4) for t in range(30, 290, 2)]
    monkeypatch.setattr(hl, "transcribe_regions", lambda *a, **k: [w for w in talk])
    monkeypatch.setattr(hl, "vod_out_dir", lambda src, root=None: tmp_path / "out")

    def fake_llm(prompt, schema, cfg):
        n = prompt.count("### Capítulo")
        return {"titulo": "Un directo épico", "miniatura": "QUÉ LOCURA", "descripcion": "Lo mejor.",
                "tags": ["#gta", "gaming"], "capitulos": [f"Momento {i}" for i in range(n)]}

    preset = load_preset("gta")
    preset["episode"] = {"target_min": 1.5, "pad": 30, "min_chapter": 20}
    plan, words = ep.plan_episode(vod, preset, call=fake_llm)
    assert plan.title == "Un directo épico" and plan.thumb_text == "QUÉ LOCURA" and plan.tags == ["gta", "gaming"]
    assert plan.chapters and all(c.title.startswith("Momento") for c in plan.chapters)
    assert plan.intro and plan.intro[0] < 108 and plan.intro[1] > 100  # el grito más fuerte
    assert (tmp_path / "out" / "youtube" / "plan.json").exists()

    plan.chapters[-1].keep = len(plan.chapters) == 1  # desmarcar uno si hay varios
    res = ep.render_episode(plan, words, preset, out_dir=tmp_path / "yt")
    info = json.loads(subprocess.run([ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                      str(res["video"])], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert v["height"] == 1080 and abs(float(info["format"]["duration"]) - res["duration"]) < 1.5
    desc = res["description"].read_text(encoding="utf-8")
    assert "00:00 Intro" in desc and "Momento 0" in desc
    assert Image.open(res["thumbnail"]).size == (1280, 720)
    assert res["srt"].read_text(encoding="utf-8").count("hola") > 5


def test_intersect():
    from pipeline.silence import intersect
    assert intersect([(0, 10), (20, 30)], [(5, 25)], 1) == [(5, 10), (20, 25)]
    assert intersect([(0, 2)], [(1.5, 3)], 1) == []


def test_dead_time_needs_silence_and_still_image(tmp_path):
    if not _have_ffmpeg():
        pytest.skip("ffmpeg no disponible")
    from pipeline.job import process_video

    # 6 s de juego en movimiento + 6 s de pantalla quieta (menú), todo en silencio.
    src = tmp_path / "menu.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6", "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=6",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
                    "-map", "[v]", "-map", "2:a", "-t", "12", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(src)], check=True)
    preset = ep._episode_preset(load_preset("gta"), ep.DEFAULTS, cut=True)
    preset["toggles"]["export_srt"] = False
    res = process_video(src, preset, words=[], output_dir=tmp_path, stem="menu")
    dur = float(subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(res.video)], capture_output=True, text=True, check=True).stdout)
    assert 5.5 <= dur <= 8.0  # se queda el juego en movimiento, se va el menú quieto
