"""Rutas del proyecto y localización de ffmpeg/ffprobe."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = ROOT / "presets"
ASSETS_DIR = ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
BIN_DIR = ROOT / "bin"
MODELS_DIR = ROOT / "models"
OUTPUT_DIR = ROOT / "output"
WORK_DIR = ROOT / "work"
CACHE_DIR = WORK_DIR / "cache"


def _find_tool(name: str) -> str:
    exe = name + (".exe" if sys.platform == "win32" else "")
    local = BIN_DIR / exe
    if local.exists():
        return str(local)
    env = os.environ.get(f"CLIPFORGE_{name.upper()}")
    if env:
        return env
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"No se encontró {exe}. Ejecuta run.bat (lo descarga en bin/) o instálalo en el PATH."
    )


def ffmpeg_bin() -> str:
    return _find_tool("ffmpeg")


def ffprobe_bin() -> str:
    return _find_tool("ffprobe")


def ensure_dirs() -> None:
    for d in (OUTPUT_DIR, WORK_DIR, CACHE_DIR, MODELS_DIR, FONTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
