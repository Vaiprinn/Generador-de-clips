from pipeline import subtitles as st
from pipeline.transcribe import Word


def _words(text, step=0.3, gap_at=None, gap=1.0):
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        if gap_at is not None and i == gap_at:
            t += gap
        out.append(Word(w, t, t + step - 0.05))
        t += step
    return out


def test_ass_color_is_bgr():
    assert st.ass_color("#FFE14D") == "&H004DE1FF"
    assert st.ass_color("rgb(255, 225, 77)") == "&H004DE1FF"
    assert st.ass_color("#000000", alpha=0x60) == "&H60000000"


def test_ass_time():
    assert st.ass_time(0) == "0:00:00.00"
    assert st.ass_time(3723.456) == "1:02:03.46"


def test_srt_time():
    assert st.srt_time(3723.456) == "01:02:03,456"


def test_group_respects_max_words_and_pauses():
    words = _words("uno dos tres cuatro cinco seis siete ocho nueve diez", gap_at=4)
    groups = st.group_phrases(words, min_words=3, max_words=5, max_chars=100)
    # [uno..cuatro] | pausa | [cinco..nueve] + "diez" (huérfana unida a la anterior)
    assert [len(g) for g in groups] == [4, 6]
    # la pausa larga antes de "cinco" siempre corta
    assert groups[0][-1].text == "cuatro"


def test_group_splits_on_strong_punctuation():
    groups = st.group_phrases(_words("qué pasa. vamos allá ahora"), max_chars=100)
    assert groups[0][-1].text == "pasa."


def test_phrases_disappear_in_silences():
    words = _words("hola qué tal", gap_at=None) + [Word("adiós", 5.0, 5.4)]
    phrases = st.build_phrases(words, {"min_words": 3, "max_words": 5, "max_chars": 30})
    assert len(phrases) == 2
    assert phrases[0].end < 1.5  # no se queda en pantalla durante el silencio
    assert phrases[1].start == 5.0


def test_phrases_never_overlap():
    words = _words("a b c d e f g h i j k l m n o p", step=0.2)
    phrases = st.build_phrases(words, {"max_words": 3, "max_chars": 30})
    for a, b in zip(phrases, phrases[1:]):
        assert a.end <= b.start


def test_format_text_case_and_punct():
    cfg = {"case": "minusculas", "strip_punctuation": True}
    assert st.format_text("Hola, ¿QUÉ tal?", cfg) == "hola ¿qué tal?"
    assert st.format_text("vamos.", {"case": "mayusculas"}) == "VAMOS"


def test_safe_position_avoids_tiktok_ui():
    x, y = st.safe_position({"pos_y": 1800, "font_size": 80}, 1080, 1920)
    assert y <= 1920 - st.SAFE_BOTTOM_PX - 80
    assert x < 540  # desplazado a la izquierda por la columna de botones


def test_build_ass_structure():
    phrases = st.build_phrases(_words("hola chicos bienvenidos"), {})
    ass = st.build_ass(phrases, {"font": "Nunito Black", "pos_y": 1100})
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Style: Frase,Nunito Black," in ass
    assert ass.count("Dialogue:") == 1
    assert "\\pos(510,1100)" in ass


def test_ass_escapes_braces():
    assert st.ass_escape("a{b}c") == "a(b)c"


def test_srt_numbering():
    phrases = st.build_phrases(_words("uno dos tres", gap_at=None) + [Word("cuatro", 4, 4.5)], {})
    srt = st.build_srt(phrases, {})
    assert srt.startswith("1\n00:00:00,000 --> ")
    assert "\n2\n" in srt
