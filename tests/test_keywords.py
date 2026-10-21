from pipeline.keywords import auto_highlight, glossary_terms, normalize
from pipeline.transcribe import Word


def _w(text):
    return [Word(t, i * 0.3, i * 0.3 + 0.25) for i, t in enumerate(text.split())]


def test_normalize_strips_accents_and_punct():
    assert normalize("¡Mbappé!") == "mbappe"


def test_glossary_terms_always_highlighted():
    words = auto_highlight(_w("vamos con franklin a por el golpe"), "Franklin, Trevor")
    assert [w.text for w in words if w.hl] == ["franklin"]


def test_exclamation_and_shouting():
    words = auto_highlight(_w("esto es INCREÍBLE de verdad"))
    assert any(w.hl and w.text == "INCREÍBLE" for w in words)


def test_stopwords_never_highlighted():
    words = auto_highlight(_w("y que de la el en"))
    assert not any(w.hl for w in words)


def test_glossary_terms_parse():
    assert {"cayo", "perico", "lspd"} <= glossary_terms("Cayo Perico, LSPD")
