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
    ass = st.build_ass(_words("hola chicos bienvenidos"), {"font": "Nunito Black", "pos_y": 1100})
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Style: Sub,Nunito Black," in ass
    assert ass.count("Dialogue:") == 1
    assert "\\pos(510,1100)" in ass


def test_ass_escapes_braces():
    assert st.ass_escape("a{b}c") == "a(b)c"


def test_srt_numbering():
    srt = st.build_srt(_words("uno dos tres", gap_at=None) + [Word("cuatro", 4, 4.5)])
    assert srt.startswith("1\n00:00:00,000 --> ")
    assert "\n2\n" in srt


def test_resaltado_colors_keywords():
    words = _words("qué locura de partida")
    words[1].hl = True
    ass = st.build_ass(words, {"style": "resaltado", "highlight_color": "#FFE14D", "case": "minusculas"})
    assert "{\\1c&H4DE1FF&}locura{\\1c&HFFFFFF&}" in ass


def test_frase_highlight_only_with_toggle():
    words = _words("qué locura de partida")
    words[1].hl = True
    assert "4DE1FF" not in st.build_ass(words, {"style": "frase"}).split("[Events]")[1]
    assert "4DE1FF" in st.build_ass(words, {"style": "frase"}, highlight=True).split("[Events]")[1]


def test_karaoke_one_event_per_word_with_pop():
    words = _words("no puede ser")
    cfg = {"style": "karaoke", "case": "mayusculas", "min_words": 1, "max_words": 3, "max_chars": 18}
    events = [l for l in st.build_ass(words, cfg).splitlines() if l.startswith("Dialogue:")]
    assert len(events) == 3
    assert "\\t(0,80,\\fscx115\\fscy115)" in events[1] and "PUEDE" in events[1]
    # cada palabra empieza cuando se dice y no se solapan
    starts = [e.split(",")[1] for e in events]
    assert starts == sorted(starts)


def test_hook_two_layers_sticker():
    hook = {"text": "una tragedia", "duration": 2.5, "pos_y": 650, "uppercase": True,
            "outline": 12, "outer": 10, "outline_color": "#E3262E"}
    ass = st.build_ass(_words("hola"), {}, hook=hook)
    assert "Dialogue: 0,0:00:00.00,0:00:02.50,GanchoFondo" in ass
    assert "Dialogue: 1,0:00:00.00,0:00:02.50,Gancho," in ass
    assert "UNA TRAGEDIA" in ass
    # contorno exterior más grueso que el rojo; ambos en cursiva (-1)
    fondo = next(l for l in ass.splitlines() if l.startswith("Style: GanchoFondo"))
    rojo = next(l for l in ass.splitlines() if l.startswith("Style: Gancho,"))
    assert float(fondo.split(",")[16]) > float(rojo.split(",")[16])
    assert rojo.split(",")[5] == "&H002E26E3"
    assert fondo.split(",")[8] == rojo.split(",")[8] == "-1"


def test_hook_whole_clip_and_line_break():
    hook = {"text": "una|tragedia", "duration": 0}
    ass = st.build_ass([], {}, hook=hook, duration=42.0)
    assert "0:00:42.00" in ass and "UNA\\NTRAGEDIA" in ass


def test_long_mode_scales_to_resolution():
    ass = st.build_ass(_words("hola chicos"), {"font_size": 54, "pos_y": 960}, 3840, 2160, short=False)
    assert "PlayResX: 3840" in ass
    assert ",108," in next(l for l in ass.splitlines() if l.startswith("Style: Sub"))


def test_subtitles_can_be_hidden_but_hook_kept():
    ass = st.build_ass(_words("hola"), {}, hook={"text": "ojo"}, show_subtitles=False)
    assert "Sub,," not in ass and "OJO" in ass
