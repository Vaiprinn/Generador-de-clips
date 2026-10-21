"""Transcripción con faster-whisper (timestamps por palabra) y caché en disco."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .paths import CACHE_DIR, MODELS_DIR

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]


@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float = 1.0
    hl: bool = False  # palabra clave resaltada


def words_to_json(words: list[Word]) -> list[dict]:
    return [asdict(w) for w in words]


def words_from_json(data: list[dict]) -> list[Word]:
    return [
        Word(str(d["text"]), float(d["start"]), float(d["end"]), float(d.get("prob", 1.0)), bool(d.get("hl", False)))
        for d in data
    ]


def _add_cuda_dll_dirs() -> None:
    """En Windows, añade al PATH las DLL de cuBLAS/cuDNN instaladas por pip (nvidia-*-cu12)."""
    if sys.platform != "win32":
        return
    import site

    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        nv = Path(sp) / "nvidia"
        if not nv.is_dir():
            continue
        for bin_dir in nv.glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


_MODEL_CACHE: dict[tuple, object] = {}


def _load_model(name: str, device: str):
    from faster_whisper import WhisperModel

    compute_type = "int8_float16" if device == "cuda" else "int8"
    key = (name, device, compute_type)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE.clear()  # solo un modelo en memoria a la vez
        _MODEL_CACHE[key] = WhisperModel(
            name, device=device, compute_type=compute_type, download_root=str(MODELS_DIR)
        )
    return _MODEL_CACHE[key]


def _cache_key(src: Path, track: int, cfg: dict) -> str:
    st = src.stat()
    raw = json.dumps(
        [str(src.resolve()), st.st_size, int(st.st_mtime), track,
         cfg.get("model"), cfg.get("language"), cfg.get("glossary", "")],
        ensure_ascii=False,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def transcribe(
    wav: Path,
    cfg: dict,
    duration: float,
    progress: ProgressFn | None = None,
) -> tuple[list[Word], str]:
    """Transcribe un WAV. Devuelve (palabras, dispositivo usado)."""
    _add_cuda_dll_dirs()
    want = cfg.get("device", "auto")
    if want == "cpu" or (want == "auto" and not cuda_available()):
        devices = ["cpu"]
    else:
        devices = ["cuda", "cpu"]  # si CUDA falla, respaldo en CPU

    glossary = (cfg.get("glossary") or "").strip()
    prompt = f"Vídeo de gaming en español. Vocabulario: {glossary}." if glossary else None

    last_err: Exception | None = None
    for device in devices:
        try:
            if progress:
                progress(0.0, f"Cargando modelo {cfg['model']} en {device}…")
            model = _load_model(cfg["model"], device)
            segments, info = model.transcribe(
                str(wav),
                language=cfg.get("language", "es"),
                word_timestamps=True,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                initial_prompt=prompt,
                beam_size=5,
                condition_on_previous_text=False,
            )
            total = duration or info.duration or 1.0
            words: list[Word] = []
            for seg in segments:  # generador perezoso: aquí es donde se transcribe de verdad
                for w in seg.words or []:
                    text = w.word.strip()
                    if text:
                        words.append(Word(text, round(w.start, 3), round(w.end, 3), round(w.probability, 3)))
                if progress:
                    progress(min(seg.end / total, 1.0), f"Transcribiendo… {seg.end:.0f}/{total:.0f} s")
            return words, device
        except Exception as e:  # p. ej. faltan DLL de CUDA → reintenta en CPU
            last_err = e
            log.warning("Transcripción en %s falló: %s", device, e)
            _MODEL_CACHE.clear()
    raise RuntimeError(f"No se pudo transcribir: {last_err}") from last_err


def transcribe_cached(
    src: Path,
    wav_factory: Callable[[], Path],
    track: int,
    cfg: dict,
    duration: float,
    progress: ProgressFn | None = None,
) -> tuple[list[Word], str]:
    """Como transcribe() pero reutiliza el resultado si el vídeo y los ajustes no cambiaron."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{_cache_key(src, track, cfg)}.json"
    if cfg.get("reuse_cache", True) and cache.exists():
        return words_from_json(json.loads(cache.read_text(encoding="utf-8"))), "caché"
    words, device = transcribe(wav_factory(), cfg, duration, progress)
    cache.write_text(json.dumps(words_to_json(words), ensure_ascii=False), encoding="utf-8")
    return words, device
