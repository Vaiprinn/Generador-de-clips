"""Render final en un solo pase de FFmpeg, con progreso."""
from __future__ import annotations

import functools
import re
import subprocess
from pathlib import Path
from typing import Callable

from .layout import FilterGraph
from .media import _NO_WINDOW, MediaInfo, run, video_encoder_args
from .paths import ffmpeg_bin

# Por encima de esta longitud el filtergraph va a un archivo (límite de línea de comandos de Windows).
MAX_INLINE_GRAPH = 6000


@functools.lru_cache(maxsize=1)
def ffmpeg_version() -> tuple[int, int]:
    """(mayor, menor). Las compilaciones "git"/"N-xxxx" se consideran recientes."""
    try:
        first = run([ffmpeg_bin(), "-hide_banner", "-version"]).stdout.splitlines()[0]
    except Exception:
        return (0, 0)
    m = re.search(r"version\s+n?(\d+)\.(\d+)", first)
    return (int(m.group(1)), int(m.group(2))) if m else (99, 0)


def build_audio(
    info: MediaInfo,
    output_track: str,
    select_expr: str | None = None,
    sfx_events: list[tuple[float, Path]] | None = None,
    sfx_volume: float = 0.7,
    first_input: int = 1,
) -> tuple[str, list[str], list[list[str]]]:
    """Devuelve (filtros de audio, args de -map, entradas extra) según pista, cortes y efectos."""
    n = len(info.audio_tracks)
    if n == 0:
        return "", [], []
    parts: list[str] = []
    if str(output_track) == "mix" and n > 1:
        ins = "".join(f"[0:a:{i}]" for i in range(n))
        parts.append(f"{ins}amix=inputs={n}:normalize=0:duration=longest[a_mix]")
        label = "[a_mix]"
    else:
        try:
            idx = int(output_track)
        except (TypeError, ValueError):
            idx = 0
        label = f"[0:a:{idx if 0 <= idx < n else 0}]"
    if select_expr:
        parts.append(f"{label}aselect='{select_expr}',asetpts=N/SR/TB[a_cut]")
        label = "[a_cut]"

    inputs: list[list[str]] = []
    if sfx_events:
        by_file: dict[Path, list[float]] = {}
        for t, p in sfx_events:
            by_file.setdefault(p, []).append(t)
        mix = [label]
        for j, (path, times) in enumerate(by_file.items()):
            inputs.append(["-i", str(path)])
            k = first_input + j
            outs = "".join(f"[sx{j}_{e}]" for e in range(len(times)))
            parts.append(f"[{k}:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit={len(times)}{outs}")
            for e, t in enumerate(times):
                ms = int(round(t * 1000))
                parts.append(f"[sx{j}_{e}]adelay={ms}:all=1,volume={sfx_volume:.2f}[sd{j}_{e}]")
                mix.append(f"[sd{j}_{e}]")
        parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first[a_sfx]")
        label = "[a_sfx]"

    if not parts:
        return "", ["-map", label.strip("[]")], []
    return ";".join(parts), ["-map", label], inputs


# Compatibilidad con la Fase 1/2.
def audio_args(info: MediaInfo, output_track: str) -> tuple[str, list[str]]:
    graph, mapping, _ = build_audio(info, output_track)
    return graph, mapping


def build_command(
    info: MediaInfo,
    fg: FilterGraph,
    out_path: Path,
    render_cfg: dict,
    output_track: str = "0",
    seek: float | None = None,
    frames: int | None = None,
    select_expr: str | None = None,
    sfx_events: list[tuple[float, Path]] | None = None,
    sfx_volume: float = 0.7,
    script_dir: Path | None = None,
) -> list[str]:
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-nostats", "-loglevel", "error"]
    if seek is not None:
        cmd += ["-ss", f"{seek:.3f}"]
    cmd += ["-i", info.path]
    for extra in fg.extra_inputs:
        cmd += extra
    graph = fg.graph
    a_map: list[str] = []
    if not frames:
        a_graph, a_map, a_inputs = build_audio(
            info, output_track, select_expr, sfx_events, sfx_volume,
            first_input=1 + len(fg.extra_inputs),
        )
        for extra in a_inputs:
            cmd += extra
        if a_graph:
            graph += ";" + a_graph
    if len(graph) > MAX_INLINE_GRAPH and script_dir is not None:
        script = Path(script_dir) / "filtergraph.txt"
        script.write_text(graph, encoding="utf-8")
        flag = "-/filter_complex" if ffmpeg_version() >= (7, 1) else "-filter_complex_script"
        cmd += [flag, str(script)]
    else:
        cmd += ["-filter_complex", graph]
    cmd += ["-map", fg.video_label]
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
