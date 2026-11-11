"""Layouts del Modo Short (1080x1920) y construcción del filtergraph de FFmpeg.

- "facecam_esquina": gameplay 9:16 a pantalla completa + facecam flotante con
  esquinas redondeadas y sombra (estilo Kesto).
- "split":           facecam arriba ocupando el ancho, gameplay abajo.
- "blur":            gameplay 16:9 centrado sobre un fondo desenfocado.
- "sin_facecam":     solo gameplay 9:16.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_W, OUT_H = 1080, 1920
LAYOUTS = ("facecam_esquina", "split", "blur", "sin_facecam", "blur_cam", "seguimiento")
# Layouts con la facecam grande arriba (centrada, redondeada) y el juego debajo o detrás.
TOP_CAM_LAYOUTS = ("blur_cam", "seguimiento")
# HUD de EA FC en una captura 16:9 [x, y, ancho, alto] (medido en EA FC 26/27, vista TV/broadcast).
HUD_SCORE_RECT = [0.049, 0.05, 0.1045, 0.076]  # marcador + reloj, arriba a la izquierda
HUD_RADAR_RECT = [0.42, 0.795, 0.16, 0.165]  # radar, abajo en el centro

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
    fit: bool = False  # el gameplay entra entero en su hueco; lo que sobra se rellena desenfocado
    hud: list = field(default_factory=list)  # [(recorte en el original, destino)] marcador, radar…
    subs_y: int | None = None  # centro vertical recomendado para los subtítulos
    hide: list = field(default_factory=list)  # zonas del original que se tapan en la capa del juego
    track: bool = False  # el recorte del gameplay se mueve (seguimiento de la acción)

    @property
    def hook_y(self) -> int:
        """Centro vertical por defecto del título gancho (debajo de la facecam)."""
        if self.type == "facecam_esquina" and self.facecam_dst:
            return min(self.facecam_dst.bottom + 110, 760)
        if self.type == "split" and self.facecam_dst:
            return self.facecam_dst.bottom + 120
        if self.type == "seguimiento" and self.facecam_dst:
            return self.facecam_dst.bottom + 30  # pisando el borde de la cámara, como en los Shorts de EA FC
        if self.type in TOP_CAM_LAYOUTS and self.facecam_dst:
            return self.facecam_dst.bottom + 95
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
    elif pos == "custom":  # colocada arrastrando en la vista previa
        x = int(_clamp(float(cfg.get("facecam_x", 0)), 0, OUT_W - w))
    else:
        x = OUT_W - w - margin
    y = int(cfg.get("facecam_y", 200))
    return Rect(x, max(0, min(y, OUT_H - h)), w, h)


FIT_TOLERANCE = 0.03  # si el recuadro casi tiene la forma del hueco, se rellena sin bordes


def gameplay_dst_aspect(src_w: int, src_h: int, cfg: dict) -> float:
    """Relación ancho/alto del hueco del gameplay en la salida para el layout de `cfg`."""
    kind = cfg.get("type", "facecam_esquina")
    if kind == "split":
        top_h = _even(OUT_H * _clamp(float(cfg.get("split_ratio", 0.42)), 0.15, 0.7))
        return OUT_W / (OUT_H - top_h)
    if kind in ("blur", "blur_cam"):
        return src_w / src_h
    return OUT_W / OUT_H


def _gameplay(src_w: int, src_h: int, cfg: dict, aspect: float) -> tuple[Rect, bool]:
    """Recorte del gameplay y si hay que encajarlo (fit) en vez de llenar el hueco.

    Con `gameplay_rect` (marcado a mano en el editor) se muestra exactamente ese recuadro;
    sin él, el recorte clásico por desplazamiento y zoom.
    """
    frac = cfg.get("gameplay_rect") or []
    if len(frac) == 4:
        rect = facecam_source_rect(src_w, src_h, frac)
        if abs((rect.w / rect.h) / aspect - 1) <= FIT_TOLERANCE:
            return cover_crop(rect, aspect), False
        return rect, True
    ox, oy, zoom = cfg.get("gameplay_offset", 0.0), cfg.get("gameplay_offset_y", 0.0), cfg.get("gameplay_zoom", 1.0)
    return aspect_crop(src_w, src_h, aspect, ox, oy, zoom), False


TOP_CAM_DEFAULT = {"blur_cam": 0.86, "seguimiento": 0.58}


def top_cam_dest(src: Rect, cfg: dict) -> tuple[Rect, Rect]:
    """Facecam grande arriba y centrada (estilo clips de EA FC). Devuelve (recorte, destino)."""
    aspect = _clamp(src.w / src.h, 4 / 3, 16 / 9)
    crop = cover_crop(src, aspect)
    default = TOP_CAM_DEFAULT.get(cfg.get("type"), 0.86)
    w = _even(OUT_W * _clamp(float(cfg.get("top_cam_width") or default), 0.4, 1.0))
    h = _even(w / aspect)
    if h > OUT_H * 0.36:
        h = _even(OUT_H * 0.36)
        crop = cover_crop(src, w / h)
    y = int(_clamp(float(cfg.get("top_cam_y", 70)), 0, OUT_H - h))
    return crop, Rect((OUT_W - w) // 2, y, w, h)


def hud_overlays(src_w: int, src_h: int, cfg: dict, cam: Rect | None, default_on: bool) -> list:
    """Marcador y radar recortados del original y recolocados en el vertical."""
    out = []
    if cfg.get("hud_score", default_on):
        s = facecam_source_rect(src_w, src_h, cfg.get("hud_score_rect") or HUD_SCORE_RECT)
        w = _even(OUT_W * float(cfg.get("hud_score_width", 0.28)))
        h = _even(w * s.h / s.w)
        if cam and cfg.get("type") == "seguimiento":
            # pegado a la cámara por su lado izquierdo, centrado en su alto
            w = min(w, max(2, cam.x - 24) // 2 * 2)
            h = _even(w * s.h / s.w)
            x, y = cam.x - w, cam.y + (cam.h - h) // 2
        elif cam:  # bajo la cámara y el gancho, a la derecha
            x, y = cam.x + cam.w - w, cam.bottom + int(cfg.get("hud_score_gap", 175))
        else:
            x, y = OUT_W - w - 60, 120
        out.append((s, Rect(int(x), int(y), w, h)))
    if cfg.get("hud_radar", default_on):
        s = facecam_source_rect(src_w, src_h, cfg.get("hud_radar_rect") or HUD_RADAR_RECT)
        w = _even(OUT_W * float(cfg.get("hud_radar_width", 0.42)))
        h = _even(w * s.h / s.w)
        out.append((s, Rect((OUT_W - w) // 2, OUT_H - h - int(cfg.get("hud_radar_bottom", 480)), w, h)))
    return out


def seguimiento_crop(src_w: int, src_h: int, cfg: dict, max_aspect_h: float) -> Rect:
    """Recorte del juego para «seguimiento»: más abierto que un 9:16 (se ve la jugada) y
    sin la franja de abajo del HUD (radar, nombre del jugador). `max_aspect_h`: alto/ancho máximo
    del hueco de destino. El zoom (0,6–3) estrecha o abre el plano; la x la mueve el seguimiento."""
    zoom = _clamp(float(cfg.get("gameplay_zoom") or 1.0), 0.6, MAX_ZOOM)
    view = _clamp(float(cfg.get("track_view", 0.5)) / zoom, 0.2, 1.0)
    w = min(_even(view * src_w), src_w - src_w % 2)
    bottom = src_h * _clamp(float(cfg.get("track_bottom", 0.79)), 0.4, 1.0)
    top = 0.0
    if cfg.get("hud_score", True):  # el marcador va aparte: el juego empieza debajo de él
        sr = cfg.get("hud_score_rect") or HUD_SCORE_RECT
        top = src_h * _clamp(float(cfg.get("track_top", sr[1] + sr[3] + 0.008)), 0.0, 0.3)
    usable = bottom - top
    h = min(_even(min(usable, w * max_aspect_h)), src_h - src_h % 2)
    x = (src_w - w) / 2 * (1 + _clamp(float(cfg.get("gameplay_offset", 0.0)), -1, 1))
    free = max(0.0, usable - h)
    y = top + free / 2 * (1 + _clamp(float(cfg.get("gameplay_offset_y", 0.0)), -1, 1))
    return Rect(int(x), int(_clamp(y, 0, src_h - h)), w, h)


def _snap_edges(r: Rect, size: tuple[int, int], pad: float = 0.012) -> Rect:
    """La webcam detectada casi nunca toca el borde exacto: se amplía un poco y, si está a menos
    de un 8 % de un borde, se lleva hasta él (si no, asoma una tira de la cámara en el juego)."""
    sw, sh = size
    if not sw or not sh:
        return r
    px, py = int(sw * pad), int(sh * pad)
    x0, y0, x1, y1 = r.x - px, r.y - py, r.x + r.w + px, r.y + r.h + py
    if x0 < sw * 0.08:
        x0 = 0
    if y0 < sh * 0.08:
        y0 = 0
    if x1 > sw * 0.92:
        x1 = sw
    if y1 > sh * 0.92:
        y1 = sh
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(sw, x1), min(sh, y1)
    return Rect(x0, y0, (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2)


def _hidden(fc_rect: Rect, hud: list, cfg: dict) -> list:
    """Lo que ya se muestra aparte (webcam, marcador, radar) se tapa en el juego para no verlo dos veces."""
    out = [hsrc for hsrc, _ in hud]
    if cfg.get("hide_cam_in_game", True) and fc_rect.w > 4 and fc_rect.h > 4:
        out.append(_snap_edges(fc_rect, cfg.get("_src", (0, 0))))
    return out


def _can_track(cfg: dict, gp: Rect, src_w: int, fit: bool) -> bool:
    """El seguimiento de «Seguimiento EA FC» vale para cualquier encuadre que recorte el juego
    (Solo juego, Esquina, Split), salvo si el recuadro se marcó a mano o se muestra entero."""
    return (cfg.get("track", "no") != "no" and not fit and len(cfg.get("gameplay_rect") or []) != 4
            and gp.w < src_w - 2)


def compute_short_layout(src_w: int, src_h: int, cfg: dict) -> ShortLayout:
    kind = cfg.get("type", "facecam_esquina")
    if kind not in LAYOUTS:
        kind = "facecam_esquina"
    cfg = {**cfg, "type": kind, "_src": (src_w, src_h)}
    aspect = gameplay_dst_aspect(src_w, src_h, cfg)
    full = Rect(0, 0, OUT_W, OUT_H)
    fc_rect = facecam_source_rect(src_w, src_h, cfg.get("facecam_rect") or [0, 0, 0.25, 0.25])
    # Lo que se muestra de la cámara: su imagen real, sin bandas negras ni el borde que se mezcla con
    # el juego (lo calcula el detector). Lo que se tapa en el juego sigue siendo el recuadro entero.
    inner = cfg.get("facecam_inner") if cfg.get("facecam_rect") else None
    fc_show = facecam_source_rect(src_w, src_h, inner) if inner and len(inner) == 4 else fc_rect

    if kind == "split":
        top_h = _even(OUT_H * _clamp(float(cfg.get("split_ratio", 0.42)), 0.15, 0.7))
        gp, fit = _gameplay(src_w, src_h, cfg, aspect)
        return ShortLayout(
            kind,
            gameplay=gp,
            gameplay_dst=Rect(0, top_h, OUT_W, OUT_H - top_h),
            facecam_src=cover_crop(fc_show, OUT_W / top_h),
            facecam_dst=Rect(0, 0, OUT_W, top_h),
            fit=fit,
            track=_can_track(cfg, gp, src_w, fit),
        )
    if kind in TOP_CAM_LAYOUTS:
        if cfg.get("facecam_rect"):
            fc_src, fc_dst = top_cam_dest(fc_show, cfg)
        else:  # sin webcam: el juego solo (y el gancho arriba)
            fc_src, fc_dst = None, None
            fc_rect = Rect(0, 0, 0, 0)
        if kind == "blur_cam":
            # Fondo desenfocado, cámara arriba y el juego (16:9, se puede acercar y mover) debajo.
            fg, _ = _gameplay(src_w, src_h, cfg, aspect)
            fg_w = OUT_W
            fg_h = _even(fg_w * fg.h / fg.w)
            top = fc_dst.bottom if fc_dst else 240
            cy = float(cfg.get("band_y", 0) or (top + (OUT_H - top) * 0.52))
            y = int(_clamp(cy - fg_h / 2, top + 140, OUT_H - fg_h - 40))
            dst = Rect(0, y, fg_w, fg_h)
            hud = hud_overlays(src_w, src_h, cfg, fc_dst, False)
            return ShortLayout(kind, gameplay=fg, gameplay_dst=dst, facecam_src=fc_src, facecam_dst=fc_dst,
                               background=gameplay_crop(src_w, src_h), hud=hud,
                               subs_y=max((fc_dst.bottom if fc_dst else 240) + 230, y - 90),
                               hide=_hidden(fc_rect, hud, cfg))
        # «seguimiento»: cámara (y marcador a su izquierda) arriba; debajo el juego con un plano
        # abierto que sigue la jugada, sobre el mismo juego desenfocado. Sin radar por defecto.
        if fc_dst and cfg.get("hud_score", True):
            sw = _even(OUT_W * float(cfg.get("hud_score_width", 0.30)))
            sw = min(sw, OUT_W - fc_dst.w - 48)
            if sw > 60:  # se desplaza la cámara a la derecha para que el grupo quede centrado
                fc_dst = Rect((OUT_W - fc_dst.w - sw) // 2 + sw, fc_dst.y, fc_dst.w, fc_dst.h)
        cfg_h = {**cfg, "hud_radar": cfg.get("hud_radar", False), "hud_score_width": cfg.get("hud_score_width", 0.30)}
        hud = hud_overlays(src_w, src_h, cfg_h, fc_dst, True)
        top = (fc_dst.bottom + 30) if fc_dst else 280
        avail = OUT_H - top - 260  # lo de abajo lo tapa la interfaz de TikTok/Shorts
        gp = seguimiento_crop(src_w, src_h, cfg, avail / OUT_W)
        dh = min(_even(OUT_W * gp.h / gp.w), avail)
        dst = Rect(0, top, OUT_W, dh)
        return ShortLayout(kind, gp, dst, fc_src, fc_dst, background=gameplay_crop(src_w, src_h), hud=hud,
                           subs_y=min(dst.bottom - 130, 1500), track=cfg.get("track", "no") != "no",
                           hide=_hidden(fc_rect, hud, cfg))
    if kind == "blur":
        fg, _ = _gameplay(src_w, src_h, cfg, aspect)  # en blur el recuadro marcado se usa tal cual
        fg_w, fg_h = OUT_W, _even(OUT_W * fg.h / fg.w)
        if fg_h > OUT_H:  # recuadro muy alto: se ajusta al alto
            fg_w, fg_h = _even(OUT_H * fg.w / fg.h), OUT_H
        y = int(_clamp(float(cfg.get("blur_fg_y", OUT_H / 2)) - fg_h / 2, 0, OUT_H - fg_h))
        return ShortLayout(
            kind, gameplay=fg, gameplay_dst=Rect((OUT_W - fg_w) // 2, y, fg_w, fg_h),
            background=gameplay_crop(src_w, src_h),
        )
    gp, fit = _gameplay(src_w, src_h, cfg, aspect)
    track = _can_track(cfg, gp, src_w, fit)
    if kind == "sin_facecam":
        hr = cfg.get("hide_rect") or []
        hide = [_snap_edges(facecam_source_rect(src_w, src_h, hr), (src_w, src_h))] if len(hr) == 4 else []
        return ShortLayout(kind, gp, full, fit=fit, track=track, hide=hide)
    return ShortLayout(kind, gp, full, fc_show, facecam_dest_rect(fc_show, cfg), fit=fit, track=track)


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
    track_file: Path | None = None  # sendcmd con la x del recorte en cada instante (seguimiento)
    intro: str = ""  # efecto de entrada del vídeo (INTRO_EFFECTS)
    hud_enable: list = field(default_factory=list)  # por cada HUD: expresión enable (None = siempre)
    zooms: list = field(default_factory=list)  # zooms elegidos a mano: (inicio, fin, x, y, ancho) en s y fracciones


INTRO_EFFECTS = ("ninguno", "zoom", "flash", "fundido", "desenfoque", "sacudida", "deslizar")


def _intro(parts: list[str], last: str, opts: FilterOptions | None, w: int, h: int) -> str:
    """Efecto de entrada en el primer medio segundo (después solo pasa el vídeo tal cual)."""
    kind = (opts.intro if opts else "") or "ninguno"
    if kind == "fundido":
        parts.append(f"{last}fade=t=in:st=0:d=0.45[intro]")
    elif kind == "flash":
        parts.append(f"{last}fade=t=in:st=0:d=0.35:color=white[intro]")
    elif kind == "desenfoque":
        # solo el primer trozo se desenfoca (trim): el resto del vídeo no paga el gblur
        parts.append(f"{last}split[in_a][in_b]")
        parts.append("[in_b]trim=end=0.7,setpts=PTS-STARTPTS,gblur=sigma=28,format=yuva420p,"
                     "fade=t=out:st=0.05:d=0.6:alpha=1[in_bl]")
        parts.append("[in_a][in_bl]overlay=eof_action=pass:format=auto[intro]")
    elif kind in ("zoom", "sacudida"):
        fps = opts.fps if opts else "30"
        if kind == "zoom":  # punch-in: empieza un 30 % más cerca y se abre
            z, dx, dy = "1+0.3*pow(max(0,1-it/0.55),2)", "0", "0"
        else:  # temblor de cámara que se apaga
            z = "1+0.07*max(0,1-it/0.6)"
            dx = "if(lt(it,0.6),26*sin(it*55)*(1-it/0.6),0)"
            dy = "if(lt(it,0.6),18*cos(it*47)*(1-it/0.6),0)"
        parts.append(f"{last}zoompan=z='{z}':x='iw/2-(iw/zoom/2)+{dx}':y='ih/2-(ih/zoom/2)+{dy}'"
                     f":d=1:s={w}x{h}:fps={fps}[intro]")
    elif kind == "deslizar":  # el vídeo sube desde abajo
        fps = opts.fps if opts else "30"
        parts.append(f"color=c=black:s={w}x{h}:r={fps}[in_bg]")
        parts.append(f"[in_bg]{last}overlay=x=0:y='if(lt(t,0.4),H*pow(1-t/0.4,2),0)':shortest=1[intro]")
    else:
        return last
    return "[intro]"


ZOOM_EASE = 0.3  # segundos que tarda en acercarse y en volver


def _zoom_moments(parts: list[str], last: str, opts: FilterOptions | None, w: int, h: int) -> str:
    """Zooms marcados a mano: en cada tramo la imagen se acerca suavemente al recuadro elegido
    (x, y, ancho en fracciones del Short; el alto sigue la forma de la pantalla) y luego vuelve."""
    if not opts or not opts.zooms:
        return last
    zs, xs, ys = [], [], []
    for a, b, x, y, zw in opts.zooms:
        zw = _clamp(float(zw), 0.2, 1.0)
        if b - a < 0.2 or zw >= 0.999:
            continue
        x, y = _clamp(float(x), 0, 1 - zw), _clamp(float(y), 0, 1 - zw)
        e = min(ZOOM_EASE, (b - a) / 2)
        r = f"min(1,min(max(0,(it-{a:.3f})/{e:.3f}),max(0,({b:.3f}-it)/{e:.3f})))"
        zs.append(f"{1 / zw - 1:.4f}*{r}")
        xs.append(f"{x:.4f}*{r}")
        ys.append(f"{y:.4f}*{r}")
    if not zs:
        return last
    fps = opts.fps
    parts.append(f"{last}zoompan=z='1+{'+'.join(zs)}':x='iw*({'+'.join(xs)})':y='ih*({'+'.join(ys)})'"
                 f":d=1:s={w}x{h}:fps={fps}[zm]")
    return "[zm]"


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


def _hide_regions(parts: list[str], label: str, rects: list, tag: str = "hd", src_w: int = 0, src_h: int = 0) -> str:
    """Tapa zonas del original (webcam, marcador, radar) clonando encima el trozo de al lado
    (césped o grada) con un desenfoque suave: se ve juego, no una caja."""
    if not rects:
        return label
    n = len(rects)
    parts.append(f"{label}split={n + 1}[{tag}b]" + "".join(f"[{tag}{i}]" for i in range(n)))
    last = f"[{tag}b]"
    for i, r in enumerate(rects):
        if r.y - r.h >= 0:
            donor = Rect(r.x, r.y - r.h, r.w, r.h)  # el trozo de encima
        elif r.x - r.w >= 0:
            donor = Rect(r.x - r.w, r.y, r.w, r.h)  # el de la izquierda
        elif not src_w or r.x + 2 * r.w <= src_w:
            donor = Rect(r.x + r.w, r.y, r.w, r.h)  # el de la derecha
        else:
            donor = r
        k = max(2, min(r.w, r.h) // (6 if donor is r else 90))
        parts.append(f"[{tag}{i}]{donor.as_crop()},boxblur={k}:2[{tag}p{i}]")
        parts.append(f"{last}[{tag}p{i}]overlay={r.x}:{r.y}[{tag}o{i}]")
        last = f"[{tag}o{i}]"
    return last


def _gp_crop(gp: Rect, opts: FilterOptions | None) -> str:
    """Recorte del gameplay; con seguimiento, su x cambia con el tiempo (sendcmd)."""
    if opts and opts.track_file:
        return f"sendcmd=f={escape_filter_path(opts.track_file)},crop@gp={gp.w}:{gp.h}:{gp.x}:{gp.y}"
    return gp.as_crop()


def _rounded_overlay(parts, inputs, last, src_label, src: Rect, dst: Rect, radius: int, shadow: bool,
                     work_dir: Path, idx: int, tag: str, enable: str | None = None) -> tuple[str, int]:
    """Pega `src` (del original) en `dst` con esquinas redondeadas y sombra opcional."""
    scale = "flags=lanczos,setsar=1"
    name = "facecam" if tag == "fc" else tag
    mask = make_round_mask(dst.w, dst.h, radius, work_dir / f"{name}_mask.png")
    inputs.append(["-loop", "1", "-i", str(mask)])
    mask_idx = idx
    idx += 1
    if shadow:
        sh = make_shadow(dst.w, dst.h, radius, work_dir / f"{name}_shadow.png")
        inputs.append(["-loop", "1", "-i", str(sh)])
        sx = dst.x - SHADOW_PAD + SHADOW_OFFSET[0]
        sy = dst.y - SHADOW_PAD + SHADOW_OFFSET[1]
        parts.append(f"{last}[{idx}:v]overlay={sx}:{sy}:shortest=1[{tag}s]")
        last = f"[{tag}s]"
        idx += 1
    parts.append(f"{src_label}{src.as_crop()},scale={dst.w}:{dst.h}:{scale},format=rgba[{tag}0]")
    parts.append(f"[{mask_idx}:v]format=gray[{tag}m]")
    parts.append(f"[{tag}0][{tag}m]alphamerge[{tag}1]")
    en = f":enable='{enable}'" if enable else ""
    parts.append(f"{last}[{tag}1]overlay={dst.x}:{dst.y}:shortest=1{en}[{tag}o]")
    return f"[{tag}o]", idx


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
    blur = int(_clamp(float(cfg.get("blur_strength", 20)), 0, 60))
    blur_f = f",boxblur={max(1, blur // 2)}:2" if blur else ""
    radius = int(cfg.get("facecam_radius", 28))
    shadow = bool(cfg.get("facecam_shadow", True))

    # Copias del vídeo original que necesita cada capa.
    names = ["gp"]
    if layout.background is not None:
        names.append("bg")
    if layout.facecam_src is not None:
        names.append("fc")
    names += [f"hud{i}" for i in range(len(layout.hud))]
    if len(names) > 1:
        parts.append(f"{src0}split={len(names)}" + "".join(f"[{n}_in]" for n in names))
        lbl = {n: f"[{n}_in]" for n in names}
    else:
        lbl = {"gp": src0}

    lbl["gp"] = _hide_regions(parts, lbl["gp"], layout.hide)

    if layout.background is not None:
        bg = layout.background
        # Desenfoque barato: se reduce, se difumina y se vuelve a ampliar.
        parts.append(
            f"{lbl['bg']}{bg.as_crop()},scale=270:480{blur_f},scale={OUT_W}:{OUT_H}:flags=bicubic,"
            f"setsar=1,eq=brightness=-0.06,format=yuv420p[bg0]"
        )
        parts.append(f"{lbl['gp']}{_gp_crop(gp, opts)},scale={dst.w}:{dst.h}:{scale}{zoom}[fg0]")
        parts.append(f"[bg0][fg0]overlay={dst.x}:{dst.y}[bgfg]")
        last = "[bgfg]"
    else:
        pad = f",pad={OUT_W}:{OUT_H}:{dst.x}:{dst.y}" if (dst.w, dst.h) != (OUT_W, OUT_H) else ""
        if layout.fit:
            # El recuadro entero, centrado en su hueco, sobre una copia desenfocada que lo llena.
            k = min(dst.w / gp.w, dst.h / gp.h)
            fw, fh = min(_even(gp.w * k), dst.w), min(_even(gp.h * k), dst.h)
            sw, sh = _even(dst.w / 4), _even(dst.h / 4)
            parts.append(f"{lbl['gp']}{gp.as_crop()},split=2[gpa][gpb]")
            parts.append(f"[gpa]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh}{blur_f},"
                         f"scale={dst.w}:{dst.h}:flags=bicubic,setsar=1,eq=brightness=-0.06,format=yuv420p[gpbg]")
            parts.append(f"[gpb]scale={fw}:{fh}:{scale}[gpfg]")
            parts.append(f"[gpbg][gpfg]overlay={(dst.w - fw) // 2}:{(dst.h - fh) // 2}[gpc]")
            parts.append(f"[gpc]null{zoom}{pad},format=yuv420p[bg0]")
        else:
            parts.append(f"{lbl['gp']}{_gp_crop(gp, opts)},scale={dst.w}:{dst.h}:{scale}{zoom}{pad},format=yuv420p[bg0]")
        last = "[bg0]"

    idx = first_input
    # HUD (marcador, radar) antes que la facecam, para que la cámara quede encima si se tocan.
    for i, (hsrc, hdst) in enumerate(layout.hud):
        en = opts.hud_enable[i] if opts and i < len(opts.hud_enable) else None
        last, idx = _rounded_overlay(parts, inputs, last, lbl[f"hud{i}"], hsrc, hdst, 10, False, work_dir, idx,
                                     f"hud{i}", en)

    if layout.facecam_src is not None and layout.type == "split":
        src, fdst = layout.facecam_src, layout.facecam_dst
        parts.append(f"{lbl['fc']}{src.as_crop()},scale={fdst.w}:{fdst.h}:{scale}[fc1]")
        parts.append(f"{last}[fc1]overlay={fdst.x}:{fdst.y}[bg2]")
        last = "[bg2]"
    elif layout.facecam_src is not None:
        last, idx = _rounded_overlay(parts, inputs, last, lbl["fc"], layout.facecam_src, layout.facecam_dst,
                                     radius, shadow, work_dir, idx, "fc")

    last = _zoom_moments(parts, last, opts, OUT_W, OUT_H)
    last = _progress_bar(parts, last, opts, OUT_W, OUT_H)
    last = _intro(parts, last, opts, OUT_W, OUT_H)
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
