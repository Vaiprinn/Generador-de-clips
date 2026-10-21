"""Utilidades de FFmpeg: inspección del vídeo, extracción de audio/fotogramas y encoder."""
from __future__ import annotations

import functools
import json
import subprocess
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .paths import ffmpeg_bin, ffprobe_bin

# Evita que se abra una ventana de consola por cada subproceso en Windows.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    proc = subprocess.run(cmd, creationflags=_NO_WINDOW, **kw)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-2000:]
        raise RuntimeError(f"Falló {Path(cmd[0]).name} (código {proc.returncode}):\n{tail}")
    return proc


@dataclass
class MediaInfo:
    path: str
    width: int
    height: int
    fps: Fraction
    duration: float
    audio_tracks: list[dict] = field(default_factory=list)

    @property
    def fps_str(self) -> str:
        return f"{self.fps.numerator}/{self.fps.denominator}"


def probe(path: str | Path) -> MediaInfo:
    out = run([
        ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ]).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError(f"{path} no tiene pista de vídeo")
    fps = Fraction(video.get("avg_frame_rate") or "0/1")
    if fps == 0:
        fps = Fraction(video.get("r_frame_rate") or "30/1")
    # Limita fracciones raras (p. ej. 2997/100 → 30000/1001 aprox.)
    fps = fps.limit_denominator(1001)
    width, height = int(video["width"]), int(video["height"])
    rotation = _rotation(video)
    if rotation in (90, 270):
        width, height = height, width
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    audio = [
        {
            "index": i,
            "codec": s.get("codec_name"),
            "channels": s.get("channels"),
            "title": (s.get("tags") or {}).get("title", ""),
        }
        for i, s in enumerate(x for x in data["streams"] if x.get("codec_type") == "audio")
    ]
    return MediaInfo(str(path), width, height, fps, duration, audio)


def _rotation(stream: dict) -> int:
    rot = (stream.get("tags") or {}).get("rotate")
    if rot is None:
        for sd in stream.get("side_data_list") or []:
            if "rotation" in sd:
                rot = sd["rotation"]
    try:
        return abs(int(float(rot))) % 360 if rot is not None else 0
    except ValueError:
        return 0


def extract_audio(src: str | Path, dst: str | Path, track: int = 0) -> Path:
    """Extrae una pista de audio a WAV mono 16 kHz (lo que espera Whisper)."""
    run([
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-map", f"0:a:{track}", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst),
    ])
    return Path(dst)


def extract_frame(src: str | Path, dst: str | Path, t: float = 0.0) -> Path:
    run([
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(t, 0):.3f}",
        "-i", str(src), "-frames:v", "1", str(dst),
    ])
    return Path(dst)


@functools.lru_cache(maxsize=None)
def _hw_encoder_ok(name: str) -> bool:
    """True si el encoder está compilado Y hay una GPU que lo acepte (prueba real de 0,2 s)."""
    try:
        encoders = run([ffmpeg_bin(), "-hide_banner", "-encoders"]).stdout
        if name not in encoders:
            return False
        run([
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=black:s=256x256:d=0.2", "-c:v", name, "-f", "null", "-",
        ])
        return True
    except Exception:
        return False


def has_nvenc() -> bool:
    """True si h264_nvenc funciona (GPU NVIDIA)."""
    return _hw_encoder_ok("h264_nvenc")


def has_amf() -> bool:
    """True si h264_amf funciona (GPU AMD Radeon)."""
    return _hw_encoder_ok("h264_amf")


def resolve_encoder(encoder: str = "auto") -> str:
    """'auto' → 'nvenc' si hay NVIDIA, si no 'amf' si hay AMD, si no 'x264'."""
    if encoder in ("nvenc", "amf", "x264"):
        return encoder
    if has_nvenc():
        return "nvenc"
    if has_amf():
        return "amf"
    return "x264"


def amf_args(qp: int) -> list[str]:
    """h264_amf con calidad constante (CQP); qp ≈ crf de x264."""
    return [
        "-c:v", "h264_amf", "-usage", "transcoding", "-quality", "quality", "-rc", "cqp",
        "-qp_i", str(qp), "-qp_p", str(qp + 2),
    ]


def video_encoder_args(encoder: str = "auto", crf: int = 20) -> list[str]:
    enc = resolve_encoder(encoder)
    if enc == "nvenc":
        return [
            "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
            "-cq", str(crf + 1), "-b:v", "0", "-profile:v", "high", "-pix_fmt", "yuv420p",
        ]
    if enc == "amf":
        return [*amf_args(crf), "-profile:v", "high", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p"]
