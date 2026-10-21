"""LLM opcional e intercambiable: Ninguno | Ollama | Claude API | OpenAI API.

Devuelve JSON con:
- correcciones de palabras mal transcritas (sin tocar los tiempos: 1 palabra → 1 palabra),
- índices de palabras clave a resaltar,
- título gancho,
- títulos, descripción y hashtags por plataforma.

Las claves van en .env (ANTHROPIC_API_KEY, OPENAI_API_KEY, OLLAMA_HOST).
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable

from .transcribe import Word

log = logging.getLogger(__name__)

PROVIDERS = ("ninguno", "ollama", "claude", "openai")
CHUNK_WORDS = 350  # palabras por petición para correcciones (vídeos largos)
PLATFORMS = ("youtube", "tiktok", "instagram")

_PLATFORM_OBJ = {
    "type": "object",
    "properties": {p: {"type": "string"} for p in PLATFORMS},
    "required": list(PLATFORMS),
    "additionalProperties": False,
}
_TAGS_OBJ = {
    "type": "object",
    "properties": {p: {"type": "array", "items": {"type": "string"}} for p in PLATFORMS},
    "required": list(PLATFORMS),
    "additionalProperties": False,
}
WORDS_PROPS = {
    "corrections": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"i": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["i", "text"],
            "additionalProperties": False,
        },
    },
    "highlight": {"type": "array", "items": {"type": "integer"}},
}
META_PROPS = {
    "hook": {"type": "string"},
    "titles": _PLATFORM_OBJ,
    "description": {"type": "string"},
    "hashtags": _TAGS_OBJ,
}


def _schema(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SYSTEM = (
    "Eres editor de vídeos de gaming en español de España. Corriges transcripciones automáticas "
    "y preparas textos para redes sociales. Respondes solo con el JSON pedido."
)


@dataclass
class LLMResult:
    corrections: dict[int, str] = field(default_factory=dict)
    highlight: list[int] = field(default_factory=list)
    hook: str = ""
    titles: dict[str, str] = field(default_factory=dict)
    description: str = ""
    hashtags: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["corrections"] = {str(k): v for k, v in self.corrections.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> "LLMResult":
        d = d or {}
        return cls(
            {int(k): v for k, v in (d.get("corrections") or {}).items()},
            [int(i) for i in d.get("highlight") or []],
            d.get("hook") or "", d.get("titles") or {}, d.get("description") or "", d.get("hashtags") or {},
        )


# ---------------------------------------------------------------- prompts

def _words_block(words: list[Word], offset: int) -> str:
    return "\n".join(f"{offset + i}: {w.text}" for i, w in enumerate(words))


def words_prompt(words: list[Word], offset: int, game: str, glossary: str) -> str:
    return (
        f"Juego/canal: {game}. Glosario (escritura correcta): {glossary or '—'}.\n\n"
        "Transcripción automática, una palabra por línea como «índice: palabra»:\n"
        f"{_words_block(words, offset)}\n\n"
        "Tareas:\n"
        "1. corrections: solo las palabras mal transcritas (ortografía, tildes, nombres del glosario). "
        "Cada corrección sustituye UNA palabra por UNA palabra con el mismo índice; no juntes ni partas "
        "palabras, porque los tiempos no pueden cambiar. No cambies muletillas ni el estilo hablado.\n"
        "2. highlight: índices de las palabras clave que merecen resaltarse en los subtítulos "
        "(como mucho una cada 4-6 palabras: momentos, nombres, emociones fuertes)."
    )


def meta_prompt(text: str, game: str, short: bool) -> str:
    kind = "un clip corto vertical (TikTok, Reels, Shorts)" if short else "un vídeo largo de YouTube"
    return (
        f"Juego/canal: {game}. Es {kind}. Transcripción completa:\n\n{text}\n\n"
        "Genera:\n"
        "- hook: título gancho de 2 a 4 palabras para la pantalla, en mayúsculas y con gancho "
        "(ej. «UNA TRAGEDIA», «NO PUEDE SER»).\n"
        "- titles: un título por plataforma (youtube, tiktok, instagram), atractivos y sin clickbait engañoso.\n"
        "- description: 2-3 frases para la descripción.\n"
        "- hashtags: 5-8 hashtags por plataforma, con #."
    )


# ---------------------------------------------------------------- proveedores

def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"El LLM no devolvió JSON: {text[:300]}")
        return json.loads(m.group(0))


def call_claude(prompt: str, schema: dict, cfg: dict) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=cfg.get("claude_model") or "claude-opus-5-5",
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # si el modelo rechaza la petición, la reintenta el modelo recomendado
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude rechazó la petición.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return _extract_json(text)


def call_openai(prompt: str, schema: dict, cfg: dict) -> dict:
    from openai import OpenAI

    client = OpenAI()
    response = client.chat.completions.create(
        model=cfg.get("openai_model") or "gpt-5-mini",
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        response_format={"type": "json_schema", "json_schema": {"name": "clipforge", "schema": schema, "strict": True}},
    )
    return _extract_json(response.choices[0].message.content or "")


def call_ollama(prompt: str, schema: dict, cfg: dict) -> dict:
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if not host.startswith("http"):
        host = "http://" + host
    body = json.dumps({
        "model": cfg.get("ollama_model") or "gemma4",
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "stream": False,
        "format": schema,
        "options": {"temperature": 0.2},
    }).encode("utf-8")
    req = urllib.request.Request(f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.loads(r.read().decode("utf-8"))
    except OSError as e:
        raise RuntimeError(f"No se pudo conectar con Ollama en {host} ({e}). ¿Está abierto?") from e
    return _extract_json(data.get("message", {}).get("content", ""))


CALLERS: dict[str, Callable[[str, dict, dict], dict]] = {
    "claude": call_claude,
    "openai": call_openai,
    "ollama": call_ollama,
}


# ---------------------------------------------------------------- orquestación

def run_llm(
    words: list[Word],
    cfg: dict,
    game: str = "",
    glossary: str = "",
    short: bool = True,
    call: Callable[[str, dict, dict], dict] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> LLMResult:
    provider = cfg.get("provider", "ninguno")
    if provider == "ninguno" and call is None:
        return LLMResult()
    call = call or CALLERS[provider]
    report = progress or (lambda f, m: None)
    res = LLMResult()
    want_words = cfg.get("correct_text", True) or cfg.get("pick_keywords", True)
    want_meta = cfg.get("propose_hook", True) or cfg.get("metadata", True)
    chunks = [words[i:i + CHUNK_WORDS] for i in range(0, len(words), CHUNK_WORDS)] if want_words else []
    n_calls = len(chunks) + (1 if want_meta else 0)
    done = 0
    for k, chunk in enumerate(chunks):
        offset = k * CHUNK_WORDS
        report(done / max(n_calls, 1), f"LLM: revisando palabras ({k + 1}/{len(chunks)})…")
        data = call(words_prompt(chunk, offset, game, glossary), _schema(WORDS_PROPS), cfg)
        for c in data.get("corrections") or []:
            try:
                i, text = int(c["i"]), str(c["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if offset <= i < offset + len(chunk) and text and " " not in text:
                res.corrections[i] = text
        res.highlight += [int(i) for i in data.get("highlight") or [] if offset <= int(i) < offset + len(chunk)]
        done += 1
    if want_meta and words:
        report(done / max(n_calls, 1), "LLM: títulos, gancho y hashtags…")
        data = call(meta_prompt(" ".join(w.text for w in words), game, short), _schema(META_PROPS), cfg)
        res.hook = str(data.get("hook") or "").strip()
        res.titles = {p: str((data.get("titles") or {}).get(p, "")) for p in PLATFORMS}
        res.description = str(data.get("description") or "")
        res.hashtags = {p: [str(t) for t in (data.get("hashtags") or {}).get(p, [])] for p in PLATFORMS}
    report(1.0, "LLM listo.")
    return res


def apply_llm(words: list[Word], res: LLMResult, cfg: dict) -> list[Word]:
    """Aplica correcciones y palabras clave a las palabras (en su sitio)."""
    if cfg.get("correct_text", True):
        for i, text in res.corrections.items():
            if 0 <= i < len(words):
                words[i].text = text
    if cfg.get("pick_keywords", True) and res.highlight:
        keep = set(res.highlight)
        for i, w in enumerate(words):
            w.hl = i in keep
    return words


def format_metadata(res: LLMResult | None) -> str:
    if res is None or not (res.titles or res.description or res.hashtags or res.hook):
        return ""
    lines = []
    if res.hook:
        lines.append(f"Gancho: {res.hook}")
    for p in PLATFORMS:
        if res.titles.get(p) or res.hashtags.get(p):
            lines.append(f"\n[{p.upper()}]")
            if res.titles.get(p):
                lines.append(f"Título: {res.titles[p]}")
            if res.hashtags.get(p):
                lines.append("Hashtags: " + " ".join(res.hashtags[p]))
    if res.description:
        lines.append(f"\nDescripción:\n{res.description}")
    return "\n".join(lines).strip()
