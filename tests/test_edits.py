"""Editor de Shorts: registro de edición, texto corregido, animaciones del gancho, entradas y sonidos."""
from pathlib import Path

from pipeline import edits, sfx
from pipeline.layout import INTRO_EFFECTS, FilterOptions, _intro
from pipeline.subtitles import build_ass
from pipeline.transcribe import Word

CFG = {"min_words": 2, "max_words": 3, "max_chars": 28}


def _words():
    texts = ["que", "golazo", "de", "mbape", "no", "puede", "ser"]
    return [Word(t, i * 0.5, i * 0.5 + 0.4, hl=(t == "golazo")) for i, t in enumerate(texts)]


def test_record_and_phrases(tmp_path):
    rec = edits.make_record(_words(), None, CFG, input_path=tmp_path / "a.mp4", trim=None,
                            hook_text="QUÉ GOLAZO", metadata="", stem="a")
    ph = edits.phrases(rec)
    assert len(ph) >= 2
    assert "*golazo*" in " ".join(p["text"] for p in ph)
    assert ph[0]["start"] == 0.0


def test_record_drops_cut_words(tmp_path):
    segs = [(0.0, 1.0), (2.0, 4.0)]  # se corta de 1 a 2 s: "de" (1.0-1.4) y "mbape" (1.5-1.9) caen fuera
    rec = edits.make_record(_words(), segs, CFG, input_path=tmp_path / "a.mp4", trim=None,
                            hook_text="", metadata="", stem="a")
    texts = [w["text"] for w in rec["words"]]
    assert "mbape" not in texts and "de" not in texts
    no = next(w for w in rec["words"] if w["text"] == "no")
    assert abs(no["o"] - 1.0) < 1e-6  # 2.0 s del original = 1.0 s del Short


def test_apply_same_count_keeps_times_and_highlight():
    rec = edits.make_record(_words(), None, CFG, input_path=Path("a.mp4"), trim=None, hook_text="", metadata="", stem="a")
    ph = edits.phrases(rec)
    texts = [p["text"] for p in ph]
    texts[1] = texts[1].replace("mbape", "*Mbappé*")
    out = edits.apply(rec, texts)
    m = next(w for w in out if w.text == "Mbappé")
    assert m.hl and m.start == 1.5
    assert all(w.grp >= 0 for w in out)


def test_apply_rewrites_and_deletes():
    rec = edits.make_record(_words(), None, CFG, input_path=Path("a.mp4"), trim=None, hook_text="", metadata="", stem="a")
    ph = edits.phrases(rec)
    texts = ["qué golazo de Mbappé, increíble"] + [""] * (len(ph) - 1)
    out = edits.apply(rec, texts)
    assert [w.text for w in out] == ["qué", "golazo", "de", "Mbappé,", "increíble"]
    assert out[0].start == 0.0 and all(a.end <= b.start + 1e-6 for a, b in zip(out, out[1:]))
    assert not any(w.hl for w in out)


def test_next_stem_and_record_path():
    assert edits.next_stem("clip") == "clip_v2"
    assert edits.next_stem("clip_v2") == "clip_v3"
    assert edits.record_path(Path("x/01_gol_short.mp4")).name == "01_gol_edit.json"


def test_hook_animations():
    for anim in ("pop", "golpe", "caida", "deslizar", "temblor", "fijo"):
        ass = build_ass([], {}, hook={"text": "QUÉ GOLAZO", "anim": anim, "pos_y": 700}, duration=10)
        line = next(l for l in ass.splitlines() if l.startswith("Dialogue: 1"))
        assert ("\\move(" in line) == (anim in ("caida", "deslizar"))
        assert ("\\pos(" in line) != (anim in ("caida", "deslizar"))


def test_golpe_long_hook_is_split_in_two_lines():
    ass = build_ass([], {}, hook={"text": "¿te pareces a Arda Güler? 😂", "anim": "golpe"}, duration=10)
    line = next(l for l in ass.splitlines() if l.startswith("Dialogue: 1"))
    assert "\\q2" in line and line.count("\\N") == 1
    short = build_ass([], {}, hook={"text": "QUÉ GOLAZO", "anim": "golpe"}, duration=10)
    assert "\\N" not in next(l for l in short.splitlines() if l.startswith("Dialogue: 1"))


def test_intro_filters():
    for kind in INTRO_EFFECTS:
        parts = []
        last = _intro(parts, "[x]", FilterOptions(intro=kind, fps="30"), 1080, 1920)
        assert (last == "[x]") == (kind == "ninguno")


def test_default_sounds_exist():
    names = sfx.list_sfx()
    for n in ("bocina.wav", "subida.wav", "ding.wav", "bajo_808.wav", "glitch.wav", "vinilo.wav", "foto.wav"):
        assert n in names
        assert (sfx.SFX_DIR / n).stat().st_size > 1000


def test_hook_sound_can_be_shortened(tmp_path):
    full = sfx.resolve("subida.wav")
    length = sfx.duration(full)
    assert length > 0.5
    cut = sfx.trimmed(full, 0.4, tmp_path / "c.wav")
    assert abs(sfx.duration(cut) - 0.4) < 0.03
    ev = sfx.build_events({"on_hook": "subida.wav"}, [], [], True, hook_file=cut)
    assert ev == [(0.0, cut)]
    assert sfx.build_events({"on_hook": "subida.wav"}, [], [], True) == [(0.0, full)]


def test_save_video_copies_and_refuses_outside_output(tmp_path, monkeypatch):
    from studio import main, server

    out = tmp_path / "output"
    out.mkdir()
    video = out / "a_short.mp4"
    video.write_bytes(b"x" * 10)
    monkeypatch.setattr(server, "OUTPUT_DIR", out)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)

    class Win:
        answer = str(tmp_path / "guardado")

        def create_file_dialog(self, *a, **k):
            return self.answer

    api = main.Api()
    api._window = Win()
    r = api.save_video(str(video))
    assert r["saved"].endswith("guardado.mp4") and (tmp_path / "guardado.mp4").read_bytes() == b"x" * 10
    Win.answer = None
    assert api.save_video(str(video)) == {"cancelled": True}
    assert "error" in api.save_video(str(tmp_path / "otro.mp4"))
