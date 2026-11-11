"""Corregir un Short ya hecho: subtítulos, gancho y efectos, y volver a renderizarlo.

Cada render de un Short deja al lado `<nombre>_edit.json` con las palabras que se usaron (en el
tiempo del vídeo de entrada), cómo se agruparon en frases y en qué segundo del Short sale cada una.
El editor de Studio enseña esas frases; al guardar, el texto corregido se reparte en el tiempo de
cada frase y el Short se vuelve a renderizar con esas palabras tal cual (sin volver a transcribir).

En el texto, *palabra* = palabra clave resaltada.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, replace
from pathlib import Path

from . import silence
from .subtitles import build_phrases
from .transcribe import Word

SUFFIX = "_edit.json"


def record_path(video: Path) -> Path:
    """`x_short.mp4` → `x_edit.json` (en la misma carpeta)."""
    stem = re.sub(r"_(short|largo)$", "", Path(video).stem)
    return Path(video).with_name(stem + SUFFIX)


def make_record(words: list[Word], segs, sub_cfg: dict, *, input_path: Path, trim, hook_text: str,
                metadata: str, stem: str) -> dict:
    """Las palabras que salen en el Short, con su frase (p) y su instante en el Short (o)."""
    visible: list[tuple[Word, float]] = []
    for w in words:
        if segs:
            s, e = silence.remap_time(w.start, segs), silence.remap_time(w.end, segs)
            if e - s <= 0.02:
                continue
        else:
            s = w.start
        visible.append((w, s))
    phrase_of: dict[int, int] = {}
    for i, p in enumerate(build_phrases([w for w, _ in visible], sub_cfg)):
        for w in p.words:
            phrase_of[id(w)] = i
    return {
        "version": 1, "input": str(input_path), "trim": list(trim) if trim else None,
        "hook": hook_text or "", "metadata": metadata or "", "stem": stem,
        "words": [{**asdict(w), "o": round(o, 3), "p": phrase_of.get(id(w), 0)} for w, o in visible],
    }


def phrases(record: dict, mark_hl: bool = True) -> list[dict]:
    """Frases para el editor: [{start (en el Short), end, text}] con *clave* marcadas
    (solo si el resaltado está encendido: si no, las marcas no se verían en el vídeo)."""
    out: list[dict] = []
    for d in record.get("words", []):
        if not out or out[-1]["p"] != d["p"]:
            out.append({"p": d["p"], "start": d["o"], "end": d["o"], "words": []})
        out[-1]["words"].append(d)
        out[-1]["end"] = d["o"] + (d["end"] - d["start"])
    return [{"start": round(f["start"], 2), "end": round(f["end"], 2),
             "text": " ".join(f"*{w['text']}*" if w.get("hl") and mark_hl else w["text"] for w in f["words"])}
            for f in out]


_TOKEN = re.compile(r"\S+")


def _tokens(text: str) -> list[tuple[str, bool]]:
    out = []
    for tok in _TOKEN.findall(text or ""):
        hl = tok.startswith("*") and tok.rstrip(".,;:!?¡¿…").endswith("*") and len(tok.strip("*")) > 0
        clean = tok.replace("*", "")
        if clean:
            out.append((clean, hl))
    return out


def apply(record: dict, texts: list[str]) -> list[Word]:
    """Palabras nuevas a partir del texto corregido de cada frase (mismo orden que phrases()).

    Si una frase tiene las mismas palabras que antes, cada una conserva su tiempo; si cambia el
    número, el texto nuevo se reparte en el tiempo que ocupaba la frase (según su longitud).
    Una frase vacía se borra.
    """
    groups: list[list[dict]] = []
    for d in record.get("words", []):
        if not groups or groups[-1][0]["p"] != d["p"]:
            groups.append([])
        groups[-1].append(d)
    words: list[Word] = []
    for i, g in enumerate(groups):
        toks = _tokens(texts[i]) if i < len(texts) else [(d["text"], bool(d.get("hl"))) for d in g]
        if not toks:
            continue
        base = [Word(d["text"], float(d["start"]), float(d["end"]), float(d.get("prob", 1.0)),
                     bool(d.get("hl")), -1, int(d.get("spk", -1))) for d in g]
        if len(toks) == len(base):
            words += [replace(w, text=t, hl=h, grp=i) for w, (t, h) in zip(base, toks)]
            continue
        a, b = base[0].start, max(base[-1].end, base[0].start + 0.2)
        weights = [len(t) + 1 for t, _ in toks]
        total, acc = sum(weights), 0.0
        for (t, h), wt in zip(toks, weights):
            s = a + (b - a) * acc / total
            acc += wt
            e = a + (b - a) * acc / total
            words.append(Word(t, round(s, 3), round(e - 0.01, 3), 1.0, h, i, base[0].spk))
    return words


def load(video: Path) -> dict:
    p = record_path(video)
    if not p.is_file():
        raise FileNotFoundError("Este Short se hizo antes de que existiera el editor: vuelve a crearlo una vez.")
    return json.loads(p.read_text(encoding="utf-8"))


def save(record: dict, video: Path) -> Path:
    p = record_path(video)
    p.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def next_stem(stem: str) -> str:
    """clip → clip_v2 → clip_v3 (cada corrección es un archivo nuevo; el anterior no se pisa)."""
    m = re.match(r"^(.*)_v(\d+)$", stem)
    return f"{m.group(1)}_v{int(m.group(2)) + 1}" if m else f"{stem}_v2"
