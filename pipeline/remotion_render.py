"""Motor de render opcional con Remotion (solo Modo Short).

Python sigue haciendo todo el trabajo "de datos" (transcripción, cortes, LLM, revisión).
Para el render:
1. FFmpeg prepara un vídeo base con los silencios ya cortados y el audio final
   (pista elegida + efectos de sonido): `base.mp4`.
2. Remotion (React, en remotion/) dibuja encima el layout, los subtítulos animados,
   el gancho, el zoom, la marca de agua y la barra, a partir de un JSON de props.

Requiere Node.js y `npm install` en remotion/ (run.bat lo hace si Node está instalado).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from . import subtitles
from .layout import OUT_H, OUT_W, Rect, ShortLayout
from .media import _NO_WINDOW, MediaInfo, has_nvenc
from .paths import FONTS_DIR, ROOT, ffmpeg_bin
from .render import build_audio, ffmpeg_version
from .transcribe import Word

REMOTION_DIR = ROOT / "remotion"
CLI = REMOTION_DIR / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
COMPOSITION = "ClipShort"
ANIMATIONS = ("pop", "slide", "fade", "none")


class RemotionUnavailable(RuntimeError):
    pass


def node_bin() -> str | None:
    return os.environ.get("CLIPFORGE_NODE") or shutil.which("node")


def status() -> tuple[bool, str]:
    """(disponible, explicación)."""
    if not node_bin():
        return False, "Falta Node.js: instálalo (winget install OpenJS.NodeJS.LTS) y vuelve a abrir run.bat."
    if not CLI.exists():
        return False, "Falta instalar Remotion: vuelve a abrir run.bat (hace «npm install» en la carpeta remotion)."
    return True, "Remotion listo."


def _require() -> str:
    ok, msg = status()
    if not ok:
        raise RemotionUnavailable(msg)
    return node_bin()  # type: ignore[return-value]


# ---------------------------------------------------------------- fuentes

_WEIGHTS = {"thin": 100, "extralight": 200, "light": 300, "regular": 400, "medium": 500,
            "semibold": 600, "bold": 700, "extrabold": 800, "black": 900, "heavy": 900}


def font_entries(public_dir: Path) -> list[dict]:
    """Copia las fuentes de assets/fonts al directorio público y describe cada una para FontFace."""
    from PIL import ImageFont

    out = []
    if not FONTS_DIR.is_dir():
        return out
    dst = public_dir / "fonts"
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(FONTS_DIR.iterdir()):
        if f.suffix.lower() not in (".ttf", ".otf"):
            continue
        try:
            family, style_name = ImageFont.truetype(str(f), 20).getname()
        except OSError:
            continue
        shutil.copy2(f, dst / f.name)
        key = (style_name or "").lower().replace(" ", "")
        italic = "italic" in key
        weight = next((w for k, w in _WEIGHTS.items() if k in key.replace("italic", "")), 400)
        css_style = "italic" if italic else "normal"
        rel = f"fonts/{f.name}"
        # Se registra con el nombre "familia estilo" (p. ej. "Nunito Black") y con la familia sola.
        full = f"{family} {style_name.replace('Italic', '').strip()}".strip()
        for fam in dict.fromkeys([full, family]):
            out.append({"family": fam, "file": rel, "weight": str(weight), "style": css_style})
    return out


# ---------------------------------------------------------------- props

def _rect(r: Rect | None) -> dict | None:
    return None if r is None else {"x": r.x, "y": r.y, "w": r.w, "h": r.h}


def subtitle_props(words: list[Word], cfg: dict, highlight: bool, short: bool = True) -> dict | None:
    phrases = []
    for p in subtitles.build_phrases(words, cfg):
        ws = []
        for w in p.words:
            text = subtitles.format_text(w.text, cfg)
            if text:
                ws.append({"text": text, "start": round(w.start, 3), "end": round(w.end, 3), "hl": bool(w.hl)})
        if ws:
            phrases.append({"start": round(p.start, 3), "end": round(p.end, 3), "words": ws})
    if not phrases:
        return None
    x, y = subtitles.safe_position(cfg, OUT_W, OUT_H, short)
    return {
        "style": cfg.get("style", "frase"),
        "phrases": phrases,
        "font": cfg.get("font", "Nunito Black"),
        "fontSize": int(cfg.get("font_size", 72)),
        "primary": cfg.get("primary_color", "#FFFFFF"),
        "outlineColor": cfg.get("outline_color", "#000000"),
        "outline": float(cfg.get("outline", 4)),
        "shadowColor": "rgba(0,0,0,0.65)",
        "shadow": float(cfg.get("shadow", 3)),
        "highlight": bool(highlight),
        "highlightColor": cfg.get("highlight_color", "#FFE14D"),
        "activeColor": cfg.get("active_color", "#39E75F"),
        "popScale": float(cfg.get("pop_scale", 115)),
        "x": x,
        "y": y,
        "maxWidth": OUT_W - subtitles.SAFE_LEFT_PX - subtitles.SAFE_RIGHT_PX,
        "animation": cfg.get("animation", "pop") if cfg.get("animation", "pop") in ANIMATIONS else "pop",
    }


def hook_props(hook: dict | None) -> dict | None:
    if not hook or not (hook.get("text") or "").strip():
        return None
    text = hook["text"].strip()
    if hook.get("uppercase", True):
        text = text.upper()
    return {
        "text": text.replace("|", "\n"),
        "font": hook.get("font", "Montserrat Black"),
        "fontSize": int(hook.get("font_size", 120)),
        "fill": hook.get("fill", "#FFFFFF"),
        "outlineColor": hook.get("outline_color", "#E3262E"),
        "outline": float(hook.get("outline", 12)),
        "outerColor": hook.get("outer_color", "#FFFFFF"),
        "outer": float(hook.get("outer", 10)),
        "y": int(hook.get("pos_y") or 650),
        "duration": float(hook.get("duration", 2.5)),
        "tilt": float(hook.get("tilt", 0) or 0),
    }


def watermark_props(wm: dict | None) -> dict | None:
    if not wm or not (wm.get("text") or "").strip():
        return None
    m = 60
    pos = wm.get("position", "centro-izq")
    size = int(wm.get("font_size", 42))
    if pos == "arriba-izq":
        x, y, al, va = m, 110, "left", "top"
    elif pos == "arriba-der":
        x, y, al, va = OUT_W - subtitles.SAFE_RIGHT_PX, 110, "right", "top"
    elif pos == "abajo-centro":
        x, y, al, va = (m + OUT_W - subtitles.SAFE_RIGHT_PX) // 2, OUT_H - subtitles.SAFE_BOTTOM_PX, "center", "bottom"
    else:
        x, y, al, va = m, OUT_H // 2, "left", "middle"
    return {
        "text": wm["text"].strip(), "font": wm.get("font", "Nunito Black"), "fontSize": size,
        "color": wm.get("color", "#FFFFFF"), "opacity": max(0.0, min(1.0, float(wm.get("opacity", 55)) / 100)),
        "x": x, "y": y, "align": al, "valign": va,
    }


def build_props(
    base: MediaInfo,
    layout: ShortLayout,
    layout_cfg: dict,
    duration: float,
    words: list[Word],
    sub_cfg: dict,
    show_subtitles: bool,
    highlight: bool,
    hook: dict | None,
    watermark: dict | None,
    progress_bar: dict | None,
    zoom_times: list[float],
    zoom_amount: float,
    fonts: list[dict],
    video_name: str = "base.mp4",
) -> dict:
    fps = float(base.fps)
    return {
        "width": OUT_W,
        "height": OUT_H,
        "fps": round(fps, 5),
        "durationInFrames": max(1, int(round(duration * fps))),
        "video": {"src": video_name, "width": base.width, "height": base.height},
        "layout": {
            "type": layout.type,
            "gameplay": _rect(layout.gameplay),
            "gameplayDst": _rect(layout.gameplay_dst),
            "facecamSrc": _rect(layout.facecam_src),
            "facecamDst": _rect(layout.facecam_dst),
            "background": _rect(layout.background),
            "radius": int(layout_cfg.get("facecam_radius", 28)),
            "shadow": bool(layout_cfg.get("facecam_shadow", True)),
            "blur": int(layout_cfg.get("blur_strength", 20)) * 2,
        },
        "subtitles": subtitle_props(words, sub_cfg, highlight) if show_subtitles else None,
        "hook": hook_props(hook),
        "watermark": watermark_props(watermark),
        "progressBar": (
            {"color": progress_bar.get("color", "#FFE14D"), "height": int(progress_bar.get("height", 10)),
             "position": progress_bar.get("position", "arriba")}
            if progress_bar else None
        ),
        "zoom": {"times": [round(t, 3) for t in zoom_times], "amount": float(zoom_amount)} if zoom_times else None,
        "fonts": fonts,
    }


# ---------------------------------------------------------------- vídeo base (FFmpeg)

def build_base_command(
    info: MediaInfo,
    out: Path,
    output_track: str,
    select_expr: str | None,
    sfx_events: list | None,
    sfx_volume: float,
    encoder: str = "auto",
    script_dir: Path | None = None,
) -> list[str]:
    """Vídeo intermedio con los cortes aplicados y el audio definitivo (sin cambiar el encuadre)."""
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-i", info.path]
    a_graph, a_map, a_inputs = build_audio(info, output_track, select_expr, sfx_events, sfx_volume, first_input=1)
    for extra in a_inputs:
        cmd += extra
    graph_parts = []
    if select_expr:
        graph_parts.append(f"[0:v]select='{select_expr}',setpts=N/FRAME_RATE/TB[v]")
    if a_graph:
        graph_parts.append(a_graph)
    graph = ";".join(graph_parts)
    if graph:
        if len(graph) > 6000 and script_dir is not None:
            script = Path(script_dir) / "base_filtergraph.txt"
            script.write_text(graph, encoding="utf-8")
            cmd += ["-/filter_complex" if ffmpeg_version() >= (7, 1) else "-filter_complex_script", str(script)]
        else:
            cmd += ["-filter_complex", graph]
    if select_expr:
        use_nvenc = encoder == "nvenc" or (encoder == "auto" and has_nvenc())
        venc = (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "17", "-b:v", "0"] if use_nvenc
                else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16"])
        cmd += ["-map", "[v]", *venc, "-pix_fmt", "yuv420p", "-r", info.fps_str]
    else:
        cmd += ["-map", "0:v:0", "-c:v", "copy"]  # sin cortes: no hace falta recodificar el vídeo
    cmd += a_map
    if a_map:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-shortest", str(out)]
    return cmd


# ---------------------------------------------------------------- CLI de Remotion

_PROGRESS = re.compile(r"(\d+)\s*/\s*(\d+)")


def _cli_base(node: str, action: str) -> list[str]:
    return [node, str(CLI), action, "src/index.ts", COMPOSITION]


def _common_flags(props_path: Path, public_dir: Path) -> list[str]:
    flags = [f"--props={props_path}", f"--public-dir={public_dir}", "--log=info"]
    chrome = os.environ.get("CLIPFORGE_CHROME")
    if chrome:
        flags.append(f"--browser-executable={chrome}")
    return flags


def render(
    props: dict,
    public_dir: Path,
    out_path: Path,
    log_path: Path,
    crf: int = 18,
    progress: Callable[[float], None] | None = None,
) -> None:
    node = _require()
    props_path = Path(public_dir) / "props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    total = int(props["durationInFrames"])
    cmd = _cli_base(node, "render") + [str(Path(out_path).resolve())]
    cmd += _common_flags(props_path, public_dir) + ["--codec=h264", f"--crf={crf}", "--overwrite"]
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        log.write(" ".join(cmd) + "\n\n")
        log.flush()
        proc = subprocess.Popen(
            cmd, cwd=REMOTION_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            if progress and "render" in line.lower():
                for done, tot in _PROGRESS.findall(line):
                    if int(tot) == total:
                        progress(min(int(done) / max(total, 1), 1.0))
        code = proc.wait()
    if code != 0 or not Path(out_path).exists():
        tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-2500:]
        raise RuntimeError(f"Remotion falló (código {code}). Log: {log_path}\n{tail}")


def still(props: dict, public_dir: Path, out_png: Path, frame: int = 0) -> Path:
    node = _require()
    props_path = Path(public_dir) / "props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    cmd = _cli_base(node, "still") + [str(Path(out_png).resolve())]
    cmd += _common_flags(props_path, public_dir) + [f"--frame={frame}", "--overwrite"]
    proc = subprocess.run(cmd, cwd=REMOTION_DIR, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", creationflags=_NO_WINDOW)
    if proc.returncode != 0 or not Path(out_png).exists():
        raise RuntimeError(f"Remotion (still) falló:\n{(proc.stdout + proc.stderr)[-2000:]}")
    return Path(out_png)
