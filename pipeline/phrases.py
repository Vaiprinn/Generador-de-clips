"""Edición manual de subtítulos a nivel de frase.

En la revisión, cada fila de la tabla «Subtítulos» es una frase (lo que aparece en
pantalla de una vez). Al cambiar su texto o sus tiempos se recalculan las palabras:

- mismo nº de palabras → se conservan los tiempos de cada palabra (el karaoke sigue
  sincronizado) y solo cambia el texto;
- distinto nº → se reparten los tiempos de la frase de forma proporcional a la
  longitud de cada palabra;
- texto vacío → se borra la frase.

Las palabras quedan con `grp` fijado, así que al renderizar se respetan esas frases
aunque cambie el estilo (botón «Reagrupar» para volver a la agrupación automática).
"""
from __future__ import annotations

from .keywords import normalize
from .subtitles import group_phrases, groups_from_ids
from .transcribe import Word


def assign_groups(words: list[Word], cfg: dict) -> list[Word]:
    """Fija `grp` según la agrupación automática del estilo actual."""
    for w in words:
        w.grp = -1
    groups = group_phrases(
        words,
        min_words=int(cfg.get("min_words", 3)),
        max_words=int(cfg.get("max_words", 6)),
        max_chars=int(cfg.get("max_chars", 28)),
    )
    for i, g in enumerate(groups):
        for w in g:
            w.grp = i
    return words


def current_groups(words: list[Word], cfg: dict) -> list[list[Word]]:
    if words and all(w.grp >= 0 for w in words):
        return groups_from_ids(words)
    return groups_from_ids(assign_groups(words, cfg))


def phrase_rows(words: list[Word], cfg: dict) -> list[list]:
    """Filas [inicio, fin, texto] para la tabla de subtítulos."""
    return [
        [round(g[0].start, 2), round(g[-1].end, 2), " ".join(w.text for w in g)]
        for g in current_groups(words, cfg)
    ]


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _rescale(t: float, old: tuple[float, float], new: tuple[float, float]) -> float:
    (a, b), (c, d) = old, new
    if b - a < 1e-6:
        return c
    return c + (t - a) * (d - c) / (b - a)


def _spread(tokens: list[str], start: float, end: float) -> list[tuple[float, float]]:
    """Reparte [start, end] entre las palabras según su longitud."""
    weights = [len(t) + 1 for t in tokens]
    total = sum(weights) or 1
    out, t = [], start
    for wgt in weights:
        dur = (end - start) * wgt / total
        out.append((round(t, 3), round(t + dur * 0.92, 3)))  # pequeño hueco entre palabras
        t += dur
    return out


def apply_phrase_rows(words: list[Word], rows, cfg: dict) -> list[Word]:
    """Aplica la tabla de subtítulos editada y devuelve la nueva lista de palabras."""
    if hasattr(rows, "values") and hasattr(rows, "columns"):  # pandas.DataFrame
        rows = rows.values.tolist()
    groups = current_groups(words, cfg)
    out: list[Word] = []
    for i, row in enumerate(rows or []):
        if not row or len(row) < 3:
            continue
        old = groups[i] if i < len(groups) else []
        o_start = old[0].start if old else 0.0
        o_end = old[-1].end if old else o_start + 1.0
        start = _num(row[0], o_start)
        end = max(_num(row[1], o_end), start + 0.1)
        tokens = str(row[2] if row[2] is not None else "").split()
        if not tokens:
            continue  # frase borrada
        if len(tokens) == len(old):
            times = [(_rescale(w.start, (o_start, o_end), (start, end)),
                      _rescale(w.end, (o_start, o_end), (start, end))) for w in old]
            hls = [w.hl for w in old]
            probs = [w.prob for w in old]
        else:
            times = _spread(tokens, start, end)
            old_hl = {normalize(w.text) for w in old if w.hl}
            hls = [normalize(t) in old_hl for t in tokens]
            probs = [1.0] * len(tokens)
        for tok, (s, e), hl, p in zip(tokens, times, hls, probs):
            out.append(Word(tok, round(s, 3), round(e, 3), p, hl, i))
    out.sort(key=lambda w: (w.start, w.grp))
    # Renumera las frases en orden de aparición (por si se reordenaron tiempos).
    remap: dict[int, int] = {}
    for w in out:
        remap.setdefault(w.grp, len(remap))
        w.grp = remap[w.grp]
    return out
