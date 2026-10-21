"""Efectos de sonido.

Los sonidos por defecto se sintetizan la primera vez (sin descargas ni licencias)
en assets/sfx/. Puedes añadir tus propios .wav/.mp3 a esa carpeta y elegirlos en la UI.
"""
from __future__ import annotations

import wave
from pathlib import Path

from .paths import ASSETS_DIR

SFX_DIR = ASSETS_DIR / "sfx"
SR = 48000
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}


def _write(path: Path, samples) -> None:
    import numpy as np

    s = np.clip(samples, -1, 1)
    stereo = np.repeat((s * 32767).astype("<i2")[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(stereo.tobytes())


def _lowpass(x, alpha):
    import numpy as np

    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        a = alpha[i] if hasattr(alpha, "__len__") else alpha
        acc += a * (v - acc)
        y[i] = acc
    return y


def _whoosh():
    import numpy as np

    n = int(SR * 0.45)
    t = np.linspace(0, 1, n)
    rng = np.random.default_rng(1)
    noise = rng.standard_normal(n)
    cutoff = 0.02 + 0.25 * np.sin(np.pi * t) ** 2  # el filtro se abre y se cierra
    y = _lowpass(noise, cutoff) * np.sin(np.pi * t) ** 1.5
    return 0.9 * y / (np.abs(y).max() + 1e-9)


def _boom():
    import numpy as np

    n = int(SR * 0.7)
    t = np.arange(n) / SR
    freq = 40 + 90 * np.exp(-t * 9)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * 5)
    click = np.random.default_rng(2).standard_normal(n) * np.exp(-t * 120) * 0.4
    y = body + click
    return 0.95 * y / (np.abs(y).max() + 1e-9)


def _pop():
    import numpy as np

    n = int(SR * 0.09)
    t = np.arange(n) / SR
    y = np.sin(2 * np.pi * (700 + 900 * np.exp(-t * 40)) * t) * np.exp(-t * 45)
    return 0.8 * y


def _swish():
    import numpy as np

    n = int(SR * 0.18)
    t = np.linspace(0, 1, n)
    noise = np.random.default_rng(3).standard_normal(n)
    hp = noise - _lowpass(noise, 0.15)
    y = hp * np.sin(np.pi * t) ** 2
    return 0.6 * y / (np.abs(y).max() + 1e-9)


DEFAULTS = {"whoosh.wav": _whoosh, "boom.wav": _boom, "pop.wav": _pop, "swish.wav": _swish}


def ensure_default_sfx() -> Path:
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in DEFAULTS.items():
        p = SFX_DIR / name
        if not p.exists():
            _write(p, fn())
    return SFX_DIR


def list_sfx() -> list[str]:
    ensure_default_sfx()
    return sorted(p.name for p in SFX_DIR.iterdir() if p.suffix.lower() in AUDIO_EXT)


def resolve(name: str) -> Path | None:
    if not name:
        return None
    ensure_default_sfx()
    p = SFX_DIR / Path(name).name
    return p if p.exists() else None


def build_events(cfg: dict, zoom_times: list[float], cuts: list[float], has_hook: bool) -> list[tuple[float, Path]]:
    """Lista (instante, archivo) de los efectos a mezclar."""
    events: list[tuple[float, Path]] = []
    if has_hook and (p := resolve(cfg.get("on_hook", ""))):
        events.append((0.0, p))
    if (p := resolve(cfg.get("on_zoom", ""))):
        events += [(t, p) for t in zoom_times]
    if (p := resolve(cfg.get("on_cut", ""))):
        events += [(max(t - 0.05, 0.0), p) for t in cuts]
    return sorted(events, key=lambda e: e[0])
