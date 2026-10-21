"""Aislar la voz con Demucs (htdemucs, solo "vocals") antes de transcribir.

Quita la música y los efectos del juego para que Whisper entienda mejor la voz.
Es opcional: `pip install demucs` (ver requirements-extras.txt). Se ejecuta como
subproceso para no cargar PyTorch en la app si no se usa.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from .media import _NO_WINDOW


class DemucsUnavailable(RuntimeError):
    pass


def demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def isolate_vocals(wav: Path, work_dir: Path, device: str = "auto") -> Path:
    """Devuelve la ruta del WAV con solo la voz."""
    if not demucs_available():
        raise DemucsUnavailable(
            "Demucs no está instalado: .venv\\Scripts\\python -m pip install demucs (ver requirements-extras.txt)"
        )
    out_dir = Path(work_dir) / "demucs"
    cmd = [sys.executable, "-m", "demucs", "-n", "htdemucs", "--two-stems", "vocals", "-o", str(out_dir)]
    if device in ("cpu", "cuda"):
        cmd += ["-d", device]
    cmd.append(str(wav))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=_NO_WINDOW)
    vocals = out_dir / "htdemucs" / Path(wav).stem / "vocals.wav"
    if proc.returncode != 0 or not vocals.exists():
        raise RuntimeError(f"Demucs falló (código {proc.returncode}):\n{(proc.stderr or '')[-1500:]}")
    return vocals
