"""Distinguir voces (quién habla en cada momento) para colorear los subtítulos por persona.

Usa sherpa-onnx (sin PyTorch, funciona sin internet una vez descargados los modelos):
segmentación de pyannote 3.0 + huellas de voz de WeSpeaker, agrupadas por parecido.
La "Voz 1" es siempre la que más habla (normalmente tú); el resto se numera por tiempo
hablado. Los modelos (~33 MB) se descargan en models/diarization/ la primera vez.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import tarfile
import urllib.request
from pathlib import Path

import numpy as np

from . import media
from .paths import CACHE_DIR, MODELS_DIR, ffmpeg_bin
from .transcribe import Word

log = logging.getLogger(__name__)

DIR = MODELS_DIR / "diarization"
SEG_DIR = DIR / "sherpa-onnx-pyannote-segmentation-3-0"
SEG_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
           "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
EMB_FILE = "wespeaker_en_voxceleb_resnet34_LM.onnx"
EMB_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/" + EMB_FILE
MAX_SPEAKERS = 4
DEFAULT_COLORS = ["#FFFFFF", "#7CE7FF", "#FFB86B", "#C6FF6B"]

Segment = tuple[float, float, int]


class SpeakersUnavailable(RuntimeError):
    pass


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ClipForge"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def ensure_models() -> tuple[Path, Path]:
    seg, emb = SEG_DIR / "model.onnx", DIR / EMB_FILE
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        if not seg.exists():
            archive = DIR / "seg.tar.bz2"
            archive.write_bytes(_download(SEG_URL))
            with tarfile.open(archive) as t:
                t.extractall(DIR, filter="data")
            archive.unlink(missing_ok=True)
        if not emb.exists():
            emb.write_bytes(_download(EMB_URL))
    except Exception as e:
        raise SpeakersUnavailable(f"No se pudieron descargar los modelos de voces ({e}).") from e
    return seg, emb


def _audio_16k(src: str | Path, track: int) -> np.ndarray:
    raw = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(src), "-map", f"0:a:{track}",
         "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, creationflags=media._NO_WINDOW,
    ).stdout
    return np.frombuffer(raw, np.float32)


def diarize(src: str | Path, track: int = 0, num_speakers: int = 0, threshold: float = 0.5) -> list[Segment]:
    """Tramos (inicio, fin, voz) con la voz 0 = la que más habla. num_speakers 0 = automático."""
    try:
        import sherpa_onnx as so
    except ImportError as e:
        raise SpeakersUnavailable("Falta sherpa-onnx: .venv\\Scripts\\python -m pip install sherpa-onnx") from e
    seg, emb = ensure_models()
    cfg = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg))),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(emb)),
        clustering=so.FastClusteringConfig(num_clusters=int(num_speakers) or -1, threshold=float(threshold)),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = so.OfflineSpeakerDiarization(cfg)
    audio = _audio_16k(src, track)
    if audio.size < 16000:
        return []
    raw = [(float(r.start), float(r.end), int(r.speaker)) for r in sd.process(audio).sort_by_start_time()]
    talk: dict[int, float] = {}
    for a, b, s in raw:
        talk[s] = talk.get(s, 0.0) + b - a
    order = {s: i for i, s in enumerate(sorted(talk, key=talk.get, reverse=True))}
    return [(round(a, 3), round(b, 3), min(order[s], MAX_SPEAKERS - 1)) for a, b, s in raw]


def diarize_cached(src: str | Path, track: int, num_speakers: int, threshold: float) -> list[Segment]:
    src = Path(src)
    st = src.stat()
    key = hashlib.sha1(json.dumps([str(src.resolve()), st.st_size, int(st.st_mtime), track,
                                   int(num_speakers), round(float(threshold), 3)]).encode()).hexdigest()[:16]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"spk_{key}.json"
    if cache.exists():
        return [tuple(s) for s in json.loads(cache.read_text(encoding="utf-8"))]
    segs = diarize(src, track, num_speakers, threshold)
    cache.write_text(json.dumps(segs), encoding="utf-8")
    return segs


def assign(words: list[Word], segments: list[Segment]) -> list[Word]:
    """Pone en cada palabra la voz con la que más se solapa (o la del tramo más cercano)."""
    if not segments:
        return words
    for w in words:
        best, best_ov = -1, 0.0
        for a, b, s in segments:
            ov = min(b, w.end) - max(a, w.start)
            if ov > best_ov:
                best, best_ov = s, ov
        if best < 0:
            mid = (w.start + w.end) / 2
            best = min(segments, key=lambda seg: min(abs(seg[0] - mid), abs(seg[1] - mid)))[2]
        w.spk = best
    return words


def speaker_count(words: list[Word]) -> int:
    return len({w.spk for w in words if w.spk >= 0})


def visible_words(words: list[Word], sub_cfg: dict) -> list[Word]:
    """Quita las palabras de las voces ocultas (p. ej. el comentarista del juego)."""
    show = list(sub_cfg.get("speaker_show") or [])
    return [w for w in words if w.spk < 0 or w.spk >= len(show) or show[w.spk]]


def color_for(spk: int, sub_cfg: dict) -> str | None:
    colors = list(sub_cfg.get("speaker_colors") or DEFAULT_COLORS)
    return colors[spk] if 0 <= spk < len(colors) else None
