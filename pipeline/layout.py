"""Layouts del Modo Short (1080x1920) y construcción del filtergraph de FFmpeg.

Fase 1: "facecam_esquina" (gameplay 9:16 a pantalla completa + facecam flotante con
esquinas redondeadas y sombra) y "sin_facecam".
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_W, OUT_H = 1080, 1920

SHADOW_PAD = 40  # margen alrededor de la facecam para que la sombra difumine
SHADOW_OFFSET = (0, 10)
SHADOW_BLUR = 16
SHADOW_OPACITY = 150  # 0-255


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    def as_crop(self) -> str:
        return f"crop={self.w}:{self.h}:{self.x}:{self.y}"


@dataclass
class ShortLayout:
    gameplay: Rect  # recorte en el vídeo original
    facecam_src: Rect | None  # recorte en el vídeo original
    facecam_dst: Rect | None  # posición/tamaño en la salida


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def gameplay_crop(src_w: int, src_h: int, offset: float = 0.0, zoom: float = 1.0) -> Rect:
    """Recorte 9:16 del vídeo original, con desplazamiento horizontal y zoom."""
    target = OUT_W / OUT_H
    zoom = max(1.0, float(zoom))
    if src_w / src_h > target:  # vídeo más ancho que 9:16 (lo normal: 16:9)
        h = src_h / zoom
        w = h * target
    else:  # vídeo ya vertical o más estrecho
        w = src_w / zoom
        h = w / target
    w, h = min(_even(w), src_w - src_w % 2), min(_even(h), src_h - src_h % 2)
    offset = max(-1.0, min(1.0, float(offset)))
    x = (src_w - w) / 2 * (1 + offset)
    y = (src_h - h) / 2
    return Rect(int(x), int(y), w, h)


def facecam_source_rect(src_w: int, src_h: int, frac: list[float]) -> Rect:
    fx, fy, fw, fh = (max(0.0, min(1.0, float(v))) for v in frac)
    w = min(_even(fw * src_w), src_w - src_w % 2)
    h = min(_even(fh * src_h), src_h - src_h % 2)
    x = min(int(fx * src_w), src_w - w)
    y = min(int(fy * src_h), src_h - h)
    return Rect(x, y, w, h)


def facecam_dest_rect(src: Rect, cfg: dict) -> Rect:
    w = _even(OUT_W * float(cfg.get("facecam_width", 0.6)))
    h = _even(w * src.h / src.w)
    margin = int(cfg.get("facecam_margin", 40))
    pos = cfg.get("facecam_position", "top-right")
    if pos == "top-left":
        x = margin
    elif pos == "top-center":
        x = (OUT_W - w) // 2
    else:
        x = OUT_W - w - margin
    y = int(cfg.get("facecam_y", 200))
    return Rect(x, max(0, min(y, OUT_H - h)), w, h)


def compute_short_layout(src_w: int, src_h: int, cfg: dict) -> ShortLayout:
    gp = gameplay_crop(src_w, src_h, cfg.get("gameplay_offset", 0.0), cfg.get("gameplay_zoom", 1.0))
    if cfg.get("type") == "sin_facecam":
        return ShortLayout(gp, None, None)
    fc_src = facecam_source_rect(src_w, src_h, cfg.get("facecam_rect", [0, 0, 0.25, 0.25]))
    return ShortLayout(gp, fc_src, facecam_dest_rect(fc_src, cfg))


# ---------------------------------------------------------------- máscaras

def make_round_mask(w: int, h: int, radius: int, path: Path) -> Path:
    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).rounded_rectangle([0, 0, w - 1, h - 1], radius=max(0, radius), fill=255)
    img.save(path)
    return path


def make_shadow(w: int, h: int, radius: int, path: Path) -> Path:
    W, H = w + 2 * SHADOW_PAD, h + 2 * SHADOW_PAD
    alpha = Image.new("L", (W, H), 0)
    ImageDraw.Draw(alpha).rounded_rectangle(
        [SHADOW_PAD, SHADOW_PAD, SHADOW_PAD + w - 1, SHADOW_PAD + h - 1],
        radius=max(0, radius),
        fill=SHADOW_OPACITY,
    )
    alpha = alpha.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.putalpha(alpha)
    img.save(path)
    return path


# ---------------------------------------------------------------- filtergraph

def escape_filter_path(path: str | Path) -> str:
    """Escapa una ruta para usarla como valor de opción en un filtergraph (Windows incluido)."""
    p = str(Path(path).resolve()).replace("\\", "/")
    p = p.replace(":", "\\:").replace("'", "\\'")
    return f"'{p}'"


@dataclass
class FilterGraph:
    graph: str
    extra_inputs: list[list[str]]  # argumentos de entrada adicionales (máscara, sombra…)
    video_label: str


def build_short_filter(
    layout: ShortLayout,
    cfg: dict,
    work_dir: Path,
    ass_path: Path | None = None,
    fonts_dir: Path | None = None,
    first_input: int = 1,
) -> FilterGraph:
    """Construye el filtergraph de un solo pase. La entrada 0 es el vídeo original."""
    parts: list[str] = []
    inputs: list[list[str]] = []
    gp = layout.gameplay
    has_fc = layout.facecam_src is not None
    src_v = "[0:v]"
    if has_fc:
        parts.append("[0:v]split=2[gp_in][fc_in]")
        src_v = "[gp_in]"
    parts.append(
        f"{src_v}{gp.as_crop()},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1,format=yuv420p[bg0]"
    )
    last = "[bg0]"

    if has_fc:
        src, dst = layout.facecam_src, layout.facecam_dst
        radius = int(cfg.get("facecam_radius", 28))
        idx = first_input
        mask = make_round_mask(dst.w, dst.h, radius, work_dir / "facecam_mask.png")
        inputs.append(["-loop", "1", "-i", str(mask)])
        mask_idx = idx
        idx += 1
        if cfg.get("facecam_shadow", True):
            shadow = make_shadow(dst.w, dst.h, radius, work_dir / "facecam_shadow.png")
            inputs.append(["-loop", "1", "-i", str(shadow)])
            sx = dst.x - SHADOW_PAD + SHADOW_OFFSET[0]
            sy = dst.y - SHADOW_PAD + SHADOW_OFFSET[1]
            parts.append(f"{last}[{idx}:v]overlay={sx}:{sy}:shortest=1[bg1]")
            last = "[bg1]"
            idx += 1
        parts.append(
            f"[fc_in]{src.as_crop()},scale={dst.w}:{dst.h}:flags=lanczos,setsar=1,format=rgba[fc0]"
        )
        parts.append(f"[{mask_idx}:v]format=gray[fcmask]")
        parts.append("[fc0][fcmask]alphamerge[fc1]")
        parts.append(f"{last}[fc1]overlay={dst.x}:{dst.y}:shortest=1[bg2]")
        last = "[bg2]"

    if ass_path is not None:
        opts = f"filename={escape_filter_path(ass_path)}"
        if fonts_dir is not None and Path(fonts_dir).is_dir():
            opts += f":fontsdir={escape_filter_path(fonts_dir)}"
        parts.append(f"{last}ass={opts}[subs]")
        last = "[subs]"

    parts.append(f"{last}format=yuv420p[vout]")
    return FilterGraph(";".join(parts), inputs, "[vout]")
