"""Layouts del Modo Short (1080x1920) y construcción del filtergraph de FFmpeg.

- "facecam_esquina": gameplay 9:16 a pantalla completa + facecam flotante con
  esquinas redondeadas y sombra (estilo Kesto).
- "split":           facecam arriba ocupando el ancho, gameplay abajo.
- "blur":            gameplay 16:9 centrado sobre un fondo desenfocado.
- "sin_facecam":     solo gameplay 9:16.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_W, OUT_H = 1080, 1920
LAYOUTS = ("facecam_esquina", "split", "blur", "sin_facecam")

SHADOW_PAD = 40  # margen alrededor de la facecam para que la sombra difumine
SHADOW_OFFSET = (0, 10)
SHADOW_BLUR = 16
SHADOW_OPACITY = 150  # 0-255
MAX_ZOOM = 3.0


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    def as_crop(self) -> str:
        return f"crop={self.w}:{self.h}:{self.x}:{self.y}"

    @property
    def bottom(self) -> int:
        return self.y + self.h


@dataclass
class ShortLayout:
    type: str
    gameplay: Rect  # recorte en el vídeo original
    gameplay_dst: Rect  # dónde va en la salida
    facecam_src: Rect | None = None  # recorte en el vídeo original
    facecam_dst: Rect | None = None  # posición/tamaño en la salida
    background: Rect | None = None  # blur: recorte para el fondo desenfocado

    @property
    def hook_y(self) -> int:
        """Centro vertical por defecto del título gancho (debajo de la facecam)."""
        if self.type == "facecam_esquina" and self.facecam_dst:
            return min(self.facecam_dst.bottom + 110, 760)
        if self.type == "split" and self.facecam_dst:
            return self.facecam_dst.bottom + 120
        if self.type == "blur":
            return max(self.gameplay_dst.y - 140, 220)
        return 620


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def aspect_crop(
    src_w: int, src_h: int, aspect: float,
    offset_x: float = 0.0, offset_y: float = 0.0, zoom: float = 1.0,
) -> Rect:
    """Recorte con la relación `aspect` (ancho/alto), desplazamiento (-1..1) y zoom (≥1)."""
    zoom = _clamp(float(zoom), 1.0, MAX_ZOOM)
    if src_w / src_h > aspect:  # el original es más ancho → se recorta a lo ancho
        h = src_h / zoom
        w = h * aspect
    else:
        w = src_w / zoom
        h = w / aspect
    w, h = min(_even(w), src_w - src_w % 2), min(_even(h), src_h - src_h % 2)
    x = (src_w - w) / 2 * (1 + _clamp(float(offset_x), -1, 1))
    y = (src_h - h) / 2 * (1 + _clamp(float(offset_y), -1, 1))
    return Rect(int(x), int(y), w, h)


def gameplay_crop(src_w: int, src_h: int, offset: float = 0.0, zoom: float = 1.0, offset_y: float = 0.0) -> Rect:
    """Recorte 9:16 del vídeo original."""
    return aspect_crop(src_w, src_h, OUT_W / OUT_H, offset, offset_y, zoom)


def gameplay_from_rect(src_w: int, src_h: int, frac: list[float], aspect: float = OUT_W / OUT_H) -> tuple[float, float, float]:
    """Convierte un rectángulo marcado a mano en (offset_x, offset_y, zoom) del recorte."""
    fx, fy, fw, fh = (_clamp(float(v), 0, 1) for v in frac)
    base = aspect_crop(src_w, src_h, aspect)
    rw, rh = max(fw * src_w, 2), max(fh * src_h, 2)
    zoom = _clamp(min(base.w / rw, base.h / rh), 1.0, MAX_ZOOM)
    cw, ch = base.w / zoom, base.h / zoom
    cx, cy = (fx + fw / 2) * src_w, (fy + fh / 2) * src_h

    def off(center, size, total):
        free = (total - size) / 2
        return _clamp((center - size / 2) / free - 1, -1, 1) if free > 1 else 0.0

    return round(off(cx, cw, src_w), 3), round(off(cy, ch, src_h), 3), round(zoom, 3)


def facecam_source_rect(src_w: int, src_h: int, frac: list[float]) -> Rect:
    fx, fy, fw, fh = (_clamp(float(v), 0, 1) for v in frac)
    w = min(_even(fw * src_w), src_w - src_w % 2)
    h = min(_even(fh * src_h), src_h - src_h % 2)
    x = min(int(fx * src_w), src_w - w)
    y = min(int(fy * src_h), src_h - h)
    return Rect(x, y, w, h)


def cover_crop(rect: Rect, aspect: float) -> Rect:
    """Recorta `rect` (centrado) para que tenga la relación `aspect`."""
    if rect.w / rect.h > aspect:
        w = _even(rect.h * aspect)
        return Rect(rect.x + (rect.w - w) // 2, rect.y, min(w, rect.w), rect.h)
    h = _even(rect.w / aspect)
    return Rect(rect.x, rect.y + (rect.h - h) // 2, rect.w, min(h, rect.h))


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
    kind = cfg.get("type", "facecam_esquina")
    if kind not in LAYOUTS:
        kind = "facecam_esquina"
    ox, oy, zoom = cfg.get("gameplay_offset", 0.0), cfg.get("gameplay_offset_y", 0.0), cfg.get("gameplay_zoom", 1.0)
    full = Rect(0, 0, OUT_W, OUT_H)
    fc_rect = facecam_source_rect(src_w, src_h, cfg.get("facecam_rect", [0, 0, 0.25, 0.25]))

    if kind == "split":
        top_h = _even(OUT_H * _clamp(float(cfg.get("split_ratio", 0.42)), 0.15, 0.7))
        bottom_h = OUT_H - top_h
        return ShortLayout(
            kind,
            gameplay=aspect_crop(src_w, src_h, OUT_W / bottom_h, ox, oy, zoom),
            gameplay_dst=Rect(0, top_h, OUT_W, bottom_h),
            facecam_src=cover_crop(fc_rect, OUT_W / top_h),
            facecam_dst=Rect(0, 0, OUT_W, top_h),
        )
    if kind == "blur":
        fg = aspect_crop(src_w, src_h, src_w / src_h, ox, oy, zoom)
        fg_h = _even(OUT_W * fg.h / fg.w)
        y = int(_clamp(float(cfg.get("blur_fg_y", OUT_H / 2)) - fg_h / 2, 0, OUT_H - fg_h))
        return ShortLayout(
            kind, gameplay=fg, gameplay_dst=Rect(0, y, OUT_W, fg_h),
            background=gameplay_crop(src_w, src_h),
        )
    gp = aspect_crop(src_w, src_h, OUT_W / OUT_H, ox, oy, zoom)
    if kind == "sin_facecam":
        return ShortLayout(kind, gp, full)
    return ShortLayout(kind, gp, full, fc_rect, facecam_dest_rect(fc_rect, cfg))


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


@dataclass
class FilterOptions:
    """Efectos de la Fase 3 que se insertan en el filtergraph."""
    select_expr: str | None = None  # corte de silencios (tramos que se conservan)
    zoom_expr: str | None = None  # auto-zoom: expresión z(it) para zoompan
    fps: str = "30"
    progress_bar: dict | None = None  # {"color", "height", "position"}
    duration: float = 0.0  # duración final (tras cortes) para la barra


def _source(parts: list[str], opts: FilterOptions | None) -> str:
    if opts and opts.select_expr:
        parts.append(f"[0:v]select='{opts.select_expr}',setpts=N/FRAME_RATE/TB[src]")
        return "[src]"
    return "[0:v]"


def _zoom(opts: FilterOptions | None, w: int, h: int) -> str:
    if opts and opts.zoom_expr:
        from .effects import zoompan_filter

        return "," + zoompan_filter(opts.zoom_expr, w, h, opts.fps)
    return ""


def _progress_bar(parts: list[str], last: str, opts: FilterOptions | None, w: int, h: int) -> str:
    if not opts or not opts.progress_bar or opts.duration <= 0:
        return last
    from .subtitles import parse_color

    cfg = opts.progress_bar
    bh = max(2, int(cfg.get("height", 10)))
    y = h - bh if cfg.get("position", "arriba") == "abajo" else 0
    r, g, b = parse_color(cfg.get("color", "#FFE14D"))
    parts.append(f"{last}drawbox=x=0:y={y}:w={w}:h={bh}:color=black@0.35:t=fill[pb0]")
    parts.append(f"color=c=0x{r:02X}{g:02X}{b:02X}:s={w}x{bh}:r={opts.fps}[pbc]")
    parts.append(f"[pb0][pbc]overlay=x='-w+W*t/{opts.duration:.3f}':y={y}:shortest=1[pb1]")
    return "[pb1]"


def _subs_and_finish(parts: list[str], last: str, ass_path: Path | None, fonts_dir: Path | None) -> str:
    if ass_path is not None:
        opts = f"filename={escape_filter_path(ass_path)}"
        if fonts_dir is not None and Path(fonts_dir).is_dir():
            opts += f":fontsdir={escape_filter_path(fonts_dir)}"
        parts.append(f"{last}ass={opts}[subs]")
        last = "[subs]"
    parts.append(f"{last}format=yuv420p[vout]")
    return ";".join(parts)


def build_short_filter(
    layout: ShortLayout,
    cfg: dict,
    work_dir: Path,
    ass_path: Path | None = None,
    fonts_dir: Path | None = None,
    first_input: int = 1,
    opts: FilterOptions | None = None,
) -> FilterGraph:
    """Construye el filtergraph de un solo pase. La entrada 0 es el vídeo original."""
    parts: list[str] = []
    inputs: list[list[str]] = []
    gp, dst = layout.gameplay, layout.gameplay_dst
    scale = "flags=lanczos,setsar=1"
    src0 = _source(parts, opts)
    zoom = _zoom(opts, dst.w, dst.h)

    if layout.type == "blur":
        bg = layout.background
        blur = int(_clamp(float(cfg.get("blur_strength", 20)), 0, 60))
        # Desenfoque barato: se reduce, se difumina y se vuelve a ampliar.
        blur_f = f",boxblur={max(1, blur // 2)}:2" if blur else ""
        parts.append(f"{src0}split=2[bg_in][fg_in]")
        parts.append(
            f"[bg_in]{bg.as_crop()},scale=270:480{blur_f},scale={OUT_W}:{OUT_H}:flags=bicubic,"
            f"setsar=1,eq=brightness=-0.06,format=yuv420p[bg0]"
        )
        parts.append(f"[fg_in]{gp.as_crop()},scale={dst.w}:{dst.h}:{scale}{zoom}[fg0]")
        parts.append(f"[bg0][fg0]overlay=0:{dst.y}[bg2]")
        last = _progress_bar(parts, "[bg2]", opts, OUT_W, OUT_H)
        return FilterGraph(_subs_and_finish(parts, last, ass_path, fonts_dir), inputs, "[vout]")

    has_fc = layout.facecam_src is not None
    src_v = src0
    if has_fc:
        parts.append(f"{src0}split=2[gp_in][fc_in]")
        src_v = "[gp_in]"
    pad = f",pad={OUT_W}:{OUT_H}:{dst.x}:{dst.y}" if (dst.w, dst.h) != (OUT_W, OUT_H) else ""
    parts.append(f"{src_v}{gp.as_crop()},scale={dst.w}:{dst.h}:{scale}{zoom}{pad},format=yuv420p[bg0]")
    last = "[bg0]"

    if has_fc and layout.type == "split":
        src, fdst = layout.facecam_src, layout.facecam_dst
        parts.append(f"[fc_in]{src.as_crop()},scale={fdst.w}:{fdst.h}:{scale}[fc1]")
        parts.append(f"{last}[fc1]overlay={fdst.x}:{fdst.y}[bg2]")
        last = "[bg2]"
    elif has_fc:
        src, fdst = layout.facecam_src, layout.facecam_dst
        radius = int(cfg.get("facecam_radius", 28))
        idx = first_input
        mask = make_round_mask(fdst.w, fdst.h, radius, work_dir / "facecam_mask.png")
        inputs.append(["-loop", "1", "-i", str(mask)])
        mask_idx = idx
        idx += 1
        if cfg.get("facecam_shadow", True):
            shadow = make_shadow(fdst.w, fdst.h, radius, work_dir / "facecam_shadow.png")
            inputs.append(["-loop", "1", "-i", str(shadow)])
            sx = fdst.x - SHADOW_PAD + SHADOW_OFFSET[0]
            sy = fdst.y - SHADOW_PAD + SHADOW_OFFSET[1]
            parts.append(f"{last}[{idx}:v]overlay={sx}:{sy}:shortest=1[bg1]")
            last = "[bg1]"
            idx += 1
        parts.append(f"[fc_in]{src.as_crop()},scale={fdst.w}:{fdst.h}:{scale},format=rgba[fc0]")
        parts.append(f"[{mask_idx}:v]format=gray[fcmask]")
        parts.append("[fc0][fcmask]alphamerge[fc1]")
        parts.append(f"{last}[fc1]overlay={fdst.x}:{fdst.y}:shortest=1[bg2]")
        last = "[bg2]"

    last = _progress_bar(parts, last, opts, OUT_W, OUT_H)
    return FilterGraph(_subs_and_finish(parts, last, ass_path, fonts_dir), inputs, "[vout]")


# ---------------------------------------------------------------- Modo Largo

def long_output_size(src_w: int, src_h: int, resolution: str = "original") -> tuple[int, int]:
    if resolution == "1080p":
        return _even(1080 * src_w / src_h), 1080
    return src_w - src_w % 2, src_h - src_h % 2


def build_long_filter(
    src_w: int, src_h: int, resolution: str,
    ass_path: Path | None = None, fonts_dir: Path | None = None,
    opts: FilterOptions | None = None,
) -> tuple[FilterGraph, tuple[int, int]]:
    """Modo Largo: se mantiene el 16:9 (o la resolución original) y se queman los subtítulos."""
    w, h = long_output_size(src_w, src_h, resolution)
    parts: list[str] = []
    src = _source(parts, opts)
    scale = f"scale={w}:{h}:flags=lanczos," if (w, h) != (src_w, src_h) else ""
    parts.append(f"{src}{scale}setsar=1{_zoom(opts, w, h)}[base]")
    last = _progress_bar(parts, "[base]", opts, w, h)
    return FilterGraph(_subs_and_finish(parts, last, ass_path, fonts_dir), [], "[vout]"), (w, h)
