"""Render final en un solo pase de FFmpeg, con progreso."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from .layout import FilterGraph
from .media import _NO_WINDOW, MediaInfo, video_encoder_args
from .paths import ffmpeg_bin


def audio_args(info: MediaInfo, output_track: str) -> tuple[str, list[str]]:
    """Devuelve (filtro de audio extra, args de -map) según la pista elegida."""
    n = len(info.audio_tracks)
    if n == 0:
        return "", []
    if str(output_track) == "mix" and n > 1:
        ins = "".join(f"[0:a:{i}]" for i in range(n))
        return f"{ins}amix=inputs={n}:normalize=0:duration=longest[aout]", ["-map", "[aout]"]
    try:
        idx = int(output_track)
    except (TypeError, ValueError):
        idx = 0
    idx = idx if 0 <= idx < n else 0
    return "", ["-map", f"0:a:{idx}"]


def build_command(
    info: MediaInfo,
    fg: FilterGraph,
    out_path: Path,
    render_cfg: dict,
    output_track: str = "0",
    seek: float | None = None,
    frames: int | None = None,
) -> list[str]:
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-nostats", "-loglevel", "error"]
    if seek is not None:
        cmd += ["-ss", f"{seek:.3f}"]
    cmd += ["-i", info.path]
    for extra in fg.extra_inputs:
        cmd += extra
    graph = fg.graph
    a_filter, a_map = ("", []) if frames else audio_args(info, output_track)
    if a_filter:
        graph += ";" + a_filter
    cmd += ["-filter_complex", graph, "-map", fg.video_label]
    if frames:
        return cmd + ["-frames:v", str(frames), str(out_path)]
    cmd += a_map
    cmd += video_encoder_args(render_cfg.get("encoder", "auto"), int(render_cfg.get("crf", 20)))
    cmd += ["-r", info.fps_str]
    if a_map:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-movflags", "+faststart", "-shortest", "-progress", "pipe:1", str(out_path)]
    return cmd


def run_ffmpeg(
    cmd: list[str],
    duration: float,
    log_path: Path,
    progress: Callable[[float], None] | None = None,
) -> None:
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        log.write(" ".join(f'"{c}"' if " " in c else c for c in cmd) + "\n\n")
        log.flush()
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=log, text=True,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if progress and line.startswith("out_time_us=") and duration > 0:
                try:
                    us = int(line.split("=", 1)[1])
                    progress(min(us / 1e6 / duration, 1.0))
                except ValueError:
                    pass
        code = proc.wait()
    if code != 0:
        tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-2500:]
        raise RuntimeError(f"FFmpeg falló (código {code}). Log: {log_path}\n{tail}")
