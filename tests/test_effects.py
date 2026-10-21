import json
import shutil
import subprocess
import wave

import numpy as np
import pytest

from pipeline import effects, sfx, silence
from pipeline.llm import LLMResult, apply_llm, format_metadata, run_llm
from pipeline.transcribe import Word

# ---------------------------------------------------------------- silencios

def test_keep_segments_with_padding():
    keeps = silence.keep_segments(10.0, [(2.0, 4.0), (8.0, 10.0)], padding=0.2)
    assert keeps == [(0.0, 2.2), (3.8, 8.2)]
    assert abs(silence.total(keeps) - 6.6) < 1e-6


def test_leading_silence_is_cut_from_zero():
    assert silence.keep_segments(5.0, [(0.0, 1.5)], padding=0.2) == [(1.3, 5.0)]


def test_remap_time_and_words():
    segs = [(0.0, 2.0), (4.0, 6.0)]
    assert silence.remap_time(1.0, segs) == 1.0
    assert silence.remap_time(3.0, segs) == 2.0  # dentro de un corte → borde
    assert silence.remap_time(5.0, segs) == 3.0
    words = [Word("a", 0.5, 1.0), Word("b", 2.5, 3.0), Word("c", 4.5, 5.0)]
    out = silence.remap_words(words, segs)
    assert [(w.text, w.start) for w in out] == [("a", 0.5), ("c", 2.5)]
    assert silence.cut_points(segs) == [2.0]


def test_silences_from_words():
    words = [Word("a", 1.0, 1.5), Word("b", 1.6, 2.0), Word("c", 4.0, 4.5)]
    assert silence.silences_from_words(words, 6.0, 0.6) == [(0.0, 1.0), (2.0, 4.0), (4.5, 6.0)]


def test_select_expr():
    assert silence.select_expr([(0, 1), (2.5, 3)]) == "between(t,0.000,1.000)+between(t,2.500,3.000)"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no instalado")
def test_detect_silences_audio(tmp_path):
    src = tmp_path / "s.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=4",
        "-f", "lavfi", "-i", "aevalsrc='if(lt(t,1),0.5*sin(2*PI*300*t),0)':s=16000:d=4",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src),
    ], check=True)
    sil = silence.detect_silences_audio(str(src), 0, -35, 0.5, 4.0)
    assert len(sil) == 1 and 0.9 < sil[0][0] < 1.2 and sil[0][1] >= 3.9

# ---------------------------------------------------------------- zoom y gritos

def _wav(path, sr=16000):
    t = np.arange(int(sr * 3)) / sr
    amp = np.where(t < 2, 0.05, 0.8)  # el último segundo es un grito
    data = (amp * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data.tobytes())
    return path


def test_shout_detection(tmp_path):
    words = [Word(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(6)]
    db = effects.word_loudness_db(_wav(tmp_path / "a.wav"), words)
    assert effects.shout_indices(db, rel_db=8) == {4, 5}


def test_zoom_times_merge_and_expr():
    words = [Word("a", 1.0, 1.2, hl=True), Word("b", 1.5, 1.7, hl=True), Word("c", 5.0, 5.3)]
    times = effects.zoom_times(words, {2}, {"min_gap": 2.0})
    assert times == [1.0, 5.0]
    expr = effects.zoom_expr(times, 1.15)
    assert expr.startswith("1+0.150*min(1,") and expr.count("clip(") == 4
    assert effects.zoom_expr([], 1.2) is None

# ---------------------------------------------------------------- efectos de sonido

def test_default_sfx_generated(tmp_path, monkeypatch):
    monkeypatch.setattr(sfx, "SFX_DIR", tmp_path / "sfx")
    names = sfx.list_sfx()
    assert {"whoosh.wav", "boom.wav", "pop.wav", "swish.wav"} <= set(names)
    with wave.open(str(tmp_path / "sfx" / "boom.wav")) as wf:
        assert wf.getframerate() == 48000 and wf.getnchannels() == 2


def test_sfx_events(tmp_path, monkeypatch):
    monkeypatch.setattr(sfx, "SFX_DIR", tmp_path / "sfx")
    ev = sfx.build_events({"on_zoom": "boom.wav", "on_hook": "whoosh.wav", "on_cut": ""}, [1.0, 3.0], [2.0], True)
    assert [(t, p.name) for t, p in ev] == [(0.0, "whoosh.wav"), (1.0, "boom.wav"), (3.0, "boom.wav")]

# ---------------------------------------------------------------- LLM (simulado)

def test_run_llm_with_fake_provider_and_apply():
    words = [Word(w, i, i + 0.5) for i, w in enumerate("vamos con franklyn a por el golpe".split())]
    prompts = []

    def fake(prompt, schema, cfg):
        prompts.append((prompt, schema))
        if "corrections" in schema["properties"]:
            return {"corrections": [{"i": 2, "text": "Franklin"}, {"i": 3, "text": "dos palabras"}],
                    "highlight": [2, 99]}
        return {"hook": "QUÉ GOLPE", "titles": {"youtube": "T1", "tiktok": "T2", "instagram": "T3"},
                "description": "Desc", "hashtags": {"youtube": ["#gta"], "tiktok": ["#fyp"], "instagram": []}}

    cfg = {"provider": "claude", "correct_text": True, "pick_keywords": True, "propose_hook": True, "metadata": True}
    res = run_llm(words, cfg, game="gta", glossary="Franklin", call=fake)
    assert res.corrections == {2: "Franklin"}  # la que partía la palabra se descarta
    assert res.highlight == [2]
    assert "2: franklyn" in prompts[0][0] and "Franklin" in prompts[0][0]
    apply_llm(words, res, cfg)
    assert words[2].text == "Franklin" and [w.hl for w in words].count(True) == 1
    meta = format_metadata(res)
    assert "QUÉ GOLPE" in meta and "#fyp" in meta and "Desc" in meta
    assert LLMResult.from_dict(json.loads(json.dumps(res.to_dict()))).corrections == {2: "Franklin"}


def test_llm_schemas_are_strict():
    from pipeline.llm import META_PROPS, WORDS_PROPS, _schema

    for s in (_schema(WORDS_PROPS), _schema(META_PROPS)):
        assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])


def test_run_llm_none_provider_is_noop():
    assert run_llm([Word("a", 0, 1)], {"provider": "ninguno"}).hook == ""


def test_ollama_unreachable_gives_clear_error(monkeypatch):
    from pipeline import llm

    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    with pytest.raises(RuntimeError, match="Ollama"):
        llm.call_ollama("hola", {"type": "object"}, {"ollama_model": "gemma4"})
