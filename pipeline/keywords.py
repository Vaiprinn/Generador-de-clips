"""Detección heurística de palabras clave para resaltar (sin LLM).

Puntúa cada palabra (glosario del juego, exclamaciones, gritos en mayúsculas,
números, nombres propios, longitud) y resalta como mucho una por frase, salvo
los términos del glosario, que se resaltan siempre. En la tabla de revisión se
puede corregir a mano; en la Fase 3 el LLM podrá proponerlas.
"""
from __future__ import annotations

import re
import unicodedata

from .transcribe import Word

STOPWORDS = set("""
a al algo algún alguna algunas alguno algunos ante antes aquel aquella aquello aquí así aun aún
bien cada como cómo con contra cual cuál cuando cuándo de del desde donde dónde dos e el él ella
ellas ello ellos en entonces entre era eran es esa esas ese eso esos esta está estaba estado están
estar estas este esto estos estoy fue fueron ha había han hasta hay he la las le les lo los más me
mi mí mientras mis mucho muy nada ni no nos nosotros o os otra otro para pero poco por porque pues
que qué quien quién se sea ser si sí sin sobre solo sólo son su sus también tan tanto te tengo
tiene tienen todo todos tu tú tus un una uno unos vale va vamos voy y ya yo eh ah oh bueno vale
tío tía tipo como cosa cosas osea o sea hacer hace hecho estamos tenemos puedo puede
""".split())

_STRIP = re.compile(r"^[¿¡\"'(\[]+|[.,;:!?…\"')\]]+$")


def normalize(text: str) -> str:
    t = _STRIP.sub("", text.strip()).lower()
    t = unicodedata.normalize("NFD", t)
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def glossary_terms(glossary: str) -> set[str]:
    terms: set[str] = set()
    for chunk in re.split(r"[,;\n]+", glossary or ""):
        for tok in chunk.split():
            n = normalize(tok)
            if len(n) >= 3 and n not in STOPWORDS:
                terms.add(n)
    return terms


def score_word(w: Word, prev: Word | None, terms: set[str]) -> int:
    raw = w.text.strip()
    n = normalize(raw)
    if not n or n in STOPWORDS:
        return 0
    s = 0
    if n in terms:
        s += 3
    if "!" in raw or "¡" in raw:
        s += 2
    letters = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", raw)
    if len(letters) >= 3 and letters.isupper():
        s += 2  # grito en mayúsculas
    if any(ch.isdigit() for ch in raw):
        s += 2
    starts_sentence = prev is None or bool(re.search(r"[.!?…]$", prev.text.strip()))
    if raw[:1].isupper() and not starts_sentence:
        s += 1  # probable nombre propio
    if len(n) >= 7:
        s += 1
    return s


def auto_highlight(words: list[Word], glossary: str = "", group_size: int = 4) -> list[Word]:
    """Marca w.hl en las palabras clave (modifica y devuelve la lista)."""
    terms = glossary_terms(glossary)
    scores = [score_word(w, words[i - 1] if i else None, terms) for i, w in enumerate(words)]
    for w in words:
        w.hl = False
    for start in range(0, len(words), group_size):
        idx = range(start, min(start + group_size, len(words)))
        best = max(idx, key=lambda i: scores[i])
        if scores[best] >= 2:
            words[best].hl = True
        for i in idx:
            if scores[i] >= 3:
                words[i].hl = True
    return words
