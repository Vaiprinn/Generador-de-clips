"""Efectos de sonido.

Los sonidos por defecto se sintetizan la primera vez (sin descargas ni licencias)
en assets/sfx/. Puedes añadir tus propios .wav/.mp3 a esa carpeta y elegirlos en la UI.
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

from . import media
from .paths import ASSETS_DIR, ffmpeg_bin, ffprobe_bin

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


def _env(n, attack, decay):
    import numpy as np

    t = np.arange(n) / SR
    return np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t * decay)


def _riser():
    """Subida de tensión: tono que sube + ruido que se abre (acaba de golpe)."""
    import numpy as np

    n = int(SR * 0.9)
    t = np.linspace(0, 1, n)
    freq = 180 * 2 ** (3 * t ** 1.6)
    tone = np.sin(2 * np.pi * np.cumsum(freq) / SR) + 0.4 * np.sin(4 * np.pi * np.cumsum(freq) / SR)
    noise = _lowpass(np.random.default_rng(4).standard_normal(n), 0.03 + 0.4 * t ** 2)
    y = (0.6 * tone + 2.5 * noise) * t ** 2
    y[-int(SR * 0.01):] *= np.linspace(1, 0, int(SR * 0.01))
    return 0.9 * y / (np.abs(y).max() + 1e-9)


def _ding():
    import numpy as np

    n = int(SR * 1.1)
    t = np.arange(n) / SR
    y = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * d) for f, a, d in
            ((1318.5, 1.0, 3.5), (2637, 0.35, 6), (3951, 0.15, 9), (1975.5, 0.25, 5)))
    return 0.7 * y / (np.abs(y).max() + 1e-9)


def _bocina():
    """Bocina de estadio (tipo airhorn): acorde de sierra con vibrato, sintetizado."""
    import numpy as np

    n = int(SR * 0.85)
    t = np.arange(n) / SR
    vib = 1 + 0.006 * np.sin(2 * np.pi * 7 * t)
    y = np.zeros(n)
    for f in (466.2, 587.3, 698.5):
        ph = np.cumsum(f * vib) / SR
        y += 2 * (ph % 1) - 1
    y = np.tanh(2.2 * _lowpass(y, 0.35))
    y *= np.minimum(1, t / 0.015) * np.where(t > 0.72, np.maximum(0, 1 - (t - 0.72) / 0.13), 1)
    return 0.75 * y / (np.abs(y).max() + 1e-9)


def _drop():
    """Bajo 808 que cae: golpe grave largo."""
    import numpy as np

    n = int(SR * 1.2)
    t = np.arange(n) / SR
    freq = 38 + 140 * np.exp(-t * 14)
    y = np.tanh(1.8 * np.sin(2 * np.pi * np.cumsum(freq) / SR)) * np.exp(-t * 2.4)
    y += np.random.default_rng(5).standard_normal(n) * np.exp(-t * 90) * 0.25
    return 0.95 * y / (np.abs(y).max() + 1e-9)


def _glitch():
    import numpy as np

    rng = np.random.default_rng(6)
    parts = []
    for _ in range(9):
        m = int(SR * rng.uniform(0.02, 0.05))
        tt = np.arange(m) / SR
        kind = rng.integers(3)
        if kind == 0:
            seg = np.sign(np.sin(2 * np.pi * rng.uniform(150, 1800) * tt))
        elif kind == 1:
            seg = rng.standard_normal(m)
        else:
            seg = np.round(np.sin(2 * np.pi * rng.uniform(60, 400) * tt) * 3) / 3
        parts.append(seg * rng.uniform(0.4, 1))
    y = np.concatenate(parts)
    return 0.6 * y / (np.abs(y).max() + 1e-9)


def _scratch():
    """Rayado de vinilo: ruido filtrado con tono que va y vuelve."""
    import numpy as np

    n = int(SR * 0.5)
    t = np.linspace(0, 1, n)
    speed = np.sin(np.pi * 2 * t) * (1 - t * 0.3)
    ph = np.cumsum(220 * (1 + 2.5 * speed)) / SR
    tone = np.sin(2 * np.pi * ph) * np.abs(speed)
    noise = _lowpass(np.random.default_rng(7).standard_normal(n), 0.08 + 0.3 * np.abs(speed)) * 3
    y = (tone + noise) * np.sin(np.pi * t) ** 0.5
    return 0.7 * y / (np.abs(y).max() + 1e-9)


def _foto():
    import numpy as np

    n = int(SR * 0.25)
    noise = np.random.default_rng(8).standard_normal(n)
    y = (noise - _lowpass(noise, 0.2)) * (_env(n, 0.001, 70))
    k = int(SR * 0.09)
    y[k:] += (noise[:-k] - _lowpass(noise[:-k], 0.2)) * _env(n - k, 0.001, 90) * 0.7
    return 0.8 * y / (np.abs(y).max() + 1e-9)


DEFAULTS = {"whoosh.wav": _whoosh, "boom.wav": _boom, "pop.wav": _pop, "swish.wav": _swish,
            "subida.wav": _riser, "ding.wav": _ding, "bocina.wav": _bocina, "bajo_808.wav": _drop,
            "glitch.wav": _glitch, "vinilo.wav": _scratch, "foto.wav": _foto}

# Nombres para la interfaz (los sonidos que añadas a assets/sfx salen con su nombre de archivo).
LABELS = {"whoosh.wav": "Whoosh", "boom.wav": "Boom", "pop.wav": "Pop", "swish.wav": "Swish",
          "subida.wav": "Subida de tensión", "ding.wav": "Ding", "bocina.wav": "Bocina de estadio",
          "bajo_808.wav": "Bajo 808", "glitch.wav": "Glitch", "vinilo.wav": "Rayado de vinilo", "foto.wav": "Foto"}


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


_DURATIONS: dict[tuple[str, float], float] = {}


def duration(path: Path) -> float:
    """Segundos que dura un sonido (0 si no se puede saber)."""
    key = (str(path), path.stat().st_mtime)
    if key not in _DURATIONS:
        out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, creationflags=media._NO_WINDOW).stdout.strip()
        try:
            _DURATIONS[key] = round(float(out), 2)
        except ValueError:
            _DURATIONS[key] = 0.0
    return _DURATIONS[key]


FADE_END = 0.12  # el sonido recortado se apaga en este tiempo: sin chasquido al cortar


def trimmed(path: Path, seconds: float, out: Path) -> Path:
    """Copia de `path` que dura `seconds` y termina con un fundido corto."""
    fade = min(FADE_END, seconds / 2)
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-t", f"{seconds:.3f}",
                    "-af", f"afade=t=out:st={seconds - fade:.3f}:d={fade:.3f}", "-ar", "48000", "-ac", "2", str(out)],
                   check=True, capture_output=True, creationflags=media._NO_WINDOW)
    return out


def build_events(cfg: dict, zoom_times: list[float], cuts: list[float], has_hook: bool,
                 hook_file: Path | None = None) -> list[tuple[float, Path]]:
    """Lista (instante, archivo) de los efectos a mezclar. `hook_file`: sonido del gancho ya recortado."""
    events: list[tuple[float, Path]] = []
    if has_hook and (p := hook_file or resolve(cfg.get("on_hook", ""))):
        events.append((0.0, p))
    if (p := resolve(cfg.get("on_zoom", ""))):
        events += [(t, p) for t in zoom_times]
    if (p := resolve(cfg.get("on_cut", ""))):
        events += [(max(t - 0.05, 0.0), p) for t in cuts]
    return sorted(events, key=lambda e: e[0])
