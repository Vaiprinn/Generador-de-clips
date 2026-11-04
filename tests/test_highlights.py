"""Buscador de momentos en VODs: señales, puntuación, LLM (falso) y render de un clip."""
import json
import subprocess

import numpy as np
import pytest

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


# ---------------------------------------------------------------- utilidades

def test_parse_and_format_time():
    assert hl.parse_time("01:12:04.3") == pytest.approx(4324.3)
    assert hl.parse_time("12:04") == 724
    assert hl.parse_time(5) == 5.0
    assert hl.fmt_time(4324.3) == "01:12:04.3"


def test_load_chat_formats(tmp_path):
    j = tmp_path / "chat.json"
    j.write_text(json.dumps([{"time_in_seconds": 10.5, "message": "hola"}, {"time_in_seconds": 3}]), encoding="utf-8")
    assert hl.load_chat(j) == [3.0, 10.5]
    jl = tmp_path / "chat.jsonl"
    jl.write_text('{"time_in_seconds": 1}\n{"time_in_seconds": 2}\n', encoding="utf-8")
    assert hl.load_chat(jl) == [1.0, 2.0]
    t = tmp_path / "chat.txt"
    t.write_text("[00:01:05] pepe: jajaja\n1:00:00 ana: gg\nsin hora\n", encoding="utf-8")
    assert hl.load_chat(t) == [65.0, 3600.0]


def test_keyword_hits():
    words = [Word("Mira", 1, 1.2), Word("esto", 1.2, 1.5), Word("normal", 5, 5.3), Word("noooooo", 8, 9),
             Word("WASTED", 12, 12.5)]
    hits = hl.keyword_hits(words, hl.keyword_list(hl.DEFAULTS))
    assert hits == [1, 8, 12]


# ---------------------------------------------------------------- puntuación

def test_scoring_finds_loud_and_chatty_window():
    dur = 600
    loud = np.full(dur, -35.0, dtype=np.float32)
    loud += np.random.default_rng(0).normal(0, 1.5, dur).astype(np.float32)
    loud[300:310] = -8.0  # gritos
    chat = [float(t) for t in np.linspace(302, 318, 40)]  # el chat explota
    wins = hl.score_windows(hl.make_windows(dur), loud, hl.DEFAULTS["weights"], chat=chat)
    best = hl.top_windows(wins, 3)
    assert any(w.start <= 300 < w.end for w in best)
    top = max(wins, key=lambda w: w.score)
    assert top.start <= 305 <= top.end and 300 <= top.peak < 310


def test_top_windows_do_not_overlap():
    wins = [hl.Window(t, t + 30, score=s) for t, s in [(0, 1), (15, 5), (30, 4), (60, 3), (200, 2)]]
    chosen = hl.top_windows(wins, 5, min_gap=0)
    for a, b in zip(chosen, chosen[1:]):
        assert a.end <= b.start


def test_regions_merge():
    wins = [hl.Window(100, 130), hl.Window(150, 180), hl.Window(1000, 1030)]
    assert hl.regions(wins, 60, 2000) == [(40.0, 240.0), (940.0, 1090.0)]


# ---------------------------------------------------------------- clips

def _talk(a: float, b: float, every: float = 0.5, pause_at: tuple = ()) -> list[Word]:
    out, t = [], a
    while t < b:
        out.append(Word("palabra", round(t, 2), round(t + 0.35, 2)))
        t += every + (1.0 if any(abs(t - p) < every / 2 for p in pause_at) else 0)
    return out


def test_snap_starts_after_pause_and_limits_length():
    words = _talk(0, 120, pause_at=(49.5,))
    c = hl.snap(hl.Clip(50.4, 200.0), words, 120, 20, 60)
    assert c is not None
    assert c.duration <= 60
    assert 49.5 <= c.start + 0.25 <= 51.0  # empieza en la palabra tras la pausa


def test_clips_from_llm_validates():
    words = _talk(0, 400)
    cands = [hl.Window(100, 130, score=3), hl.Window(300, 330, score=2)]
    data = {"clips": [
        {"tramo": 0, "inicio": 95, "fin": 135, "puntuacion": 9, "por_que": "muere 3 veces", "hook": "3 INTENTOS",
         "titulo": "Este jefe me odia", "hashtags": ["zelda", "#gaming"]},
        {"tramo": 0, "inicio": 100, "fin": 130, "puntuacion": 5, "por_que": "repetido", "hook": "", "titulo": "",
         "hashtags": []},  # se solapa con el anterior → fuera
        {"tramo": 1, "inicio": "00:05:10", "fin": "00:05:40", "puntuacion": 7, "por_que": "persecución",
         "hook": "WASTED", "titulo": "La poli", "hashtags": []},
        {"tramo": 9, "inicio": "x", "fin": 1, "puntuacion": 1, "por_que": "", "hook": "", "titulo": "", "hashtags": []},
    ]}
    clips = hl.clips_from_llm(data, cands, words, hl.DEFAULTS, 400)
    assert [c.hook for c in clips] == ["3 INTENTOS", "WASTED"]
    assert clips[0].hashtags == ["#zelda", "#gaming"]
    assert all(20 - 0.5 <= c.duration <= 60 for c in clips)
    assert 300 <= clips[1].start + 1 and clips[1].end <= 345


def test_clips_from_signals():
    cands = [hl.Window(100, 130, score=3, peak=115, signals={"volumen": 10}),
             hl.Window(500, 530, score=1, peak=510, signals={"volumen": 4})]
    clips = hl.clips_from_signals(cands, [], hl.DEFAULTS, 900)
    assert len(clips) == 2 and clips[0].score == 10
    assert clips[0].start < 115 < clips[0].end


def test_clip_words_shifts_times():
    words = [Word("a", 9, 9.5), Word("b", 10.2, 10.6), Word("c", 30, 30.5)]
    out = hl.clip_words(words, 10, 20)
    assert [(w.text, w.start) for w in out] == [("b", 0.2)]


# ---------------------------------------------------------------- de extremo a extremo (FFmpeg real, sin Whisper)

@pytest.fixture(scope="module")
def vod(tmp_path_factory):
    if not _have_ffmpeg():
        pytest.skip("ffmpeg no disponible")
    d = tmp_path_factory.mktemp("vod")
    src = d / "directo zelda.mp4"
    # 4 min: ruido suave y un «grito» fuerte entre 150 y 158 s; dos pistas (mezcla y micro).
    mic = "anoisesrc=d=240:c=pink:a=0.01,volume='if(between(t,150,158),40,1)':eval=frame"
    subprocess.run([
        ffmpeg_bin(), "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=240",
        "-f", "lavfi", "-i", mic, "-f", "lavfi", "-i", mic,
        "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-preset", "ultrafast", "-g", "60",
        "-c:a", "aac", "-shortest", str(src),
    ], check=True)
    return src


def test_loudness_per_second(vod):
    loud = hl.loudness_per_second(vod, 1, 240)
    assert 238 <= loud.size <= 241
    assert loud[152] - np.median(loud) > 20


def test_find_and_render(vod, tmp_path, monkeypatch):
    talk = _talk(120, 200, pause_at=(139.5,))
    monkeypatch.setattr(hl, "transcribe_regions", lambda *a, **k: talk)
    seen = {}

    def fake_llm(prompt, schema, cfg):
        seen["prompt"] = prompt
        assert schema["properties"]["clips"]["type"] == "array"
        return {"clips": [{"tramo": 0, "inicio": 140, "fin": 170, "puntuacion": 9, "por_que": "grita",
                           "hook": "NO PUEDE SER", "titulo": "El grito", "hashtags": ["#zelda"]}]}

    preset = load_preset("gta")
    preset["audio"]["transcribe_track"] = 1
    preset["highlights"] = {"transcribe": "candidatos", "candidates": 3, "max_clips": 2}
    preset["toggles"]["hook_title"] = True
    res = hl.find_highlights(vod, preset, call=fake_llm, out_root=tmp_path)
    assert "Tramo 0" in seen["prompt"]
    assert any(w.start <= 152 < w.end for w in res.windows)
    assert len(res.clips) == 1 and res.clips[0].hook == "NO PUEDE SER"
    saved = json.loads((res.out_dir / "candidatos.json").read_text(encoding="utf-8"))
    assert saved["clips"][0]["title"] == "El grito"

    out = hl.render_clips(vod, res.clips, res.words, preset, res.out_dir)
    clip, job = out[0]
    assert not isinstance(job, Exception), job
    info = json.loads(subprocess.run([ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                      str(job.video)], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert abs(float(info["format"]["duration"]) - clip.duration) < 1.0
    assert "El grito" in (job.video.parent / f"{job.video.stem.removesuffix('_short')}_meta.txt").read_text(encoding="utf-8")
