from pipeline import layout as L
from pipeline import silence, speakers, subtitles
from pipeline.transcribe import Word, collapse_repeats, initial_prompt


def test_clip_to_trims_whole_video():
    assert silence.clip_to(None, 5, 20, 30) == [(5.0, 20.0)]
    assert silence.clip_to(None, 0, 30, 30) is None  # el vídeo entero: sin select
    assert silence.clip_to(None, 0, 0, 30) is None  # fin 0 = hasta el final


def test_clip_to_intersects_silence_cuts():
    segs = [(0.0, 4.0), (6.0, 12.0), (15.0, 30.0)]
    assert silence.clip_to(segs, 3, 16, 30) == [(3.0, 4.0), (6.0, 12.0), (15.0, 16.0)]


def test_collapse_repeats_removes_hallucinated_runs():
    words = [Word("espérate,", i, i + 0.5) for i in range(7)] + [Word("vamos", 8, 8.5)]
    out = collapse_repeats(words)
    assert [w.text for w in out] == ["espérate,"] * 3 + ["vamos"]


def test_dialect_prompt_includes_glossary():
    p = initial_prompt({"dialect": "co", "glossary": "Arda Güler"})
    assert "parce" in p and "Arda Güler" in p


def test_assign_speakers_by_overlap():
    words = [Word("hola", 0.1, 0.5), Word("bro", 2.1, 2.4), Word("qué", 5.0, 5.2)]
    speakers.assign(words, [(0.0, 1.0, 0), (2.0, 3.0, 1)])
    assert [w.spk for w in words] == [0, 1, 1]  # la última, sin tramo: el más cercano


def test_hidden_speaker_is_not_subtitled():
    words = [Word("yo", 0, 0.4, spk=0), Word("comentarista", 1, 1.5, spk=1)]
    out = speakers.visible_words(words, {"speaker_show": [True, False, True, True]})
    assert [w.text for w in out] == ["yo"]


def test_phrases_split_and_color_by_speaker():
    words = [Word("hola", 0, 0.3, spk=0), Word("qué", 0.35, 0.5, spk=0), Word("tal", 0.55, 0.7, spk=1),
             Word("bien", 0.75, 0.9, spk=1)]
    groups = subtitles.group_phrases(words, min_words=1, max_words=6, max_chars=40)
    assert [[w.text for w in g] for g in groups] == [["hola", "qué"], ["tal", "bien"]]
    cfg = {"style": "frase", "color_by_speaker": True, "speaker_colors": ["#FFFFFF", "#7CE7FF"]}
    ass = subtitles.build_ass(words, cfg)
    assert "\\1c&HFFE77C&" in ass  # #7CE7FF en BGR


def test_custom_facecam_position():
    lay = L.compute_short_layout(1920, 1080, {"type": "facecam_esquina", "facecam_position": "custom",
                                              "facecam_x": 100, "facecam_y": 700, "facecam_width": 0.5})
    assert (lay.facecam_dst.x, lay.facecam_dst.y, lay.facecam_dst.w) == (100, 700, 540)
