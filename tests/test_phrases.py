from pipeline.phrases import apply_phrase_rows, assign_groups, phrase_rows
from pipeline.subtitles import build_phrases, build_srt
from pipeline.transcribe import Word

CFG = {"min_words": 2, "max_words": 3, "max_chars": 40}


def _w(text, step=0.3):
    return [Word(t, round(i * step, 3), round(i * step + step - 0.05, 3)) for i, t in enumerate(text.split())]


def test_rows_reflect_groups():
    rows = phrase_rows(_w("hola chicos qué tal estáis hoy"), CFG)
    assert [r[2] for r in rows] == ["hola chicos qué", "tal estáis hoy"]
    assert rows[0][0] == 0.0


def test_same_word_count_keeps_word_timings():
    words = _w("hola chicos qué tal estáis hoy")
    rows = phrase_rows(words, CFG)
    rows[0][2] = "ola chicos ké"  # corrección manual
    new = apply_phrase_rows(words, rows, CFG)
    assert [w.text for w in new[:3]] == ["ola", "chicos", "ké"]
    assert [w.start for w in new[:3]] == [0.0, 0.3, 0.6]


def test_different_word_count_spreads_times_inside_phrase():
    words = _w("hola chicos qué tal estáis hoy")
    rows = phrase_rows(words, CFG)
    rows[1][2] = "cómo estáis todos vosotros hoy"
    new = apply_phrase_rows(words, rows, CFG)
    second = [w for w in new if w.grp == 1]
    assert [w.text for w in second] == "cómo estáis todos vosotros hoy".split()
    assert second[0].start >= rows[1][0] - 1e-6 and second[-1].end <= rows[1][1] + 1e-6
    assert all(a.end <= b.start for a, b in zip(second, second[1:]))


def test_empty_text_deletes_phrase_and_groups_are_kept_at_render():
    words = _w("hola chicos qué tal estáis hoy vamos allá ya")
    rows = phrase_rows(words, CFG)
    rows[1][2] = ""
    new = apply_phrase_rows(words, rows, CFG)
    assert [w.grp for w in new] == [0, 0, 0, 1, 1, 1]
    # aunque el estilo agrupe de 1 en 1, se respetan las frases editadas
    phrases = build_phrases(new, {"max_words": 1})
    assert [len(p.words) for p in phrases] == [3, 3]
    assert build_srt(new).count("-->") == 2


def test_edit_phrase_times_rescales_words():
    words = _w("uno dos tres")
    rows = phrase_rows(words, CFG)
    rows[0][0], rows[0][1] = 1.0, 2.0
    new = apply_phrase_rows(words, rows, CFG)
    assert new[0].start == 1.0 and abs(new[-1].end - 2.0) < 0.01


def test_assign_groups_regroups():
    words = assign_groups(_w("a b c d e f g"), {"min_words": 1, "max_words": 2, "max_chars": 40})
    assert max(w.grp for w in words) >= 2


def test_highlight_survives_text_change():
    words = _w("qué locura de partida")
    words[1].hl = True
    rows = phrase_rows(words, {"max_words": 4, "max_chars": 40})
    rows[0][2] = "pero qué locura de partida"
    new = apply_phrase_rows(words, rows, {"max_words": 4, "max_chars": 40})
    assert [w.text for w in new if w.hl] == ["locura"]
