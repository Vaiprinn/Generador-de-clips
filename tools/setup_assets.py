"""Descarga lo que no va en el repositorio: ffmpeg.exe/ffprobe.exe (Windows) y fuentes libres.

Uso:  python tools/setup_assets.py [--force]
Es idempotente: solo descarga lo que falta. Si algo falla, avisa y sigue.
"""
from __future__ import annotations

import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.paths import BIN_DIR, FONTS_DIR, MODELS_DIR  # noqa: E402

FFMPEG_URLS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
]

# Fuentes OFL (gratis). Nombre de archivo → URLs alternativas.
FONTS = {
    "Nunito-Black.ttf": [
        "https://cdn.jsdelivr.net/fontsource/fonts/nunito@latest/latin-900-normal.ttf",
    ],
    "Montserrat-Black.ttf": [
        "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Black.ttf",
        "https://cdn.jsdelivr.net/fontsource/fonts/montserrat@latest/latin-900-normal.ttf",
    ],
    "Montserrat-BlackItalic.ttf": [
        "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-BlackItalic.ttf",
        "https://cdn.jsdelivr.net/fontsource/fonts/montserrat@latest/latin-900-italic.ttf",
    ],
}

UA = {"User-Agent": "ClipForge-setup/1.0"}


def _download(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        while chunk := r.read(1 << 20):
            buf.write(chunk)
            if total > 5_000_000:
                print(f"\r    {buf.tell() / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
        if total > 5_000_000:
            print()
        return buf.getvalue()


def setup_ffmpeg(force: bool = False) -> None:
    if sys.platform != "win32":
        print("[ffmpeg] No es Windows: usa el ffmpeg del sistema (apt/brew).")
        return
    exe, probe = BIN_DIR / "ffmpeg.exe", BIN_DIR / "ffprobe.exe"
    if exe.exists() and probe.exists() and not force:
        print("[ffmpeg] Ya está en bin/.")
        return
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    for url in FFMPEG_URLS:
        try:
            print(f"[ffmpeg] Descargando {url}")
            data = _download(url, timeout=300)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for member in z.namelist():
                    name = Path(member).name.lower()
                    if name in ("ffmpeg.exe", "ffprobe.exe"):
                        with z.open(member) as src, open(BIN_DIR / name, "wb") as dst:
                            shutil.copyfileobj(src, dst)
            if exe.exists() and probe.exists():
                print("[ffmpeg] OK → bin/ffmpeg.exe, bin/ffprobe.exe")
                return
        except Exception as e:
            print(f"[ffmpeg] Falló: {e}")
    print("[ffmpeg] AVISO: no se pudo descargar. Copia ffmpeg.exe y ffprobe.exe en bin/ a mano.")


def setup_fonts(force: bool = False) -> None:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    for fname, urls in FONTS.items():
        dst = FONTS_DIR / fname
        if dst.exists() and not force:
            continue
        for url in urls:
            try:
                data = _download(url)
                if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
                    raise ValueError("no es un archivo de fuente")
                dst.write_bytes(data)
                print(f"[fuentes] OK → {fname}")
                break
            except Exception as e:
                print(f"[fuentes] {fname}: {url} falló ({e})")
        else:
            print(f"[fuentes] AVISO: falta {fname}; se usará una fuente del sistema.")


def setup_face_model(force: bool = False) -> None:
    from pipeline.facedetect import YUNET_FILE, YUNET_URL

    dst = MODELS_DIR / YUNET_FILE
    if dst.exists() and not force:
        return
    try:
        data = _download(YUNET_URL)
        if len(data) < 100_000:
            raise ValueError("descarga incompleta")
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        print(f"[caras] OK → models/{YUNET_FILE}")
    except Exception as e:
        print(f"[caras] AVISO: no se pudo descargar el detector de caras ({e}); se reintentará al autodetectar.")


if __name__ == "__main__":
    force = "--force" in sys.argv
    setup_ffmpeg(force)
    setup_fonts(force)
    setup_face_model(force)
