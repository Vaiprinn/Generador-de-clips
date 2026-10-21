"""ClipForge — interfaz Gradio (localhost).

Ejecuta:  python app.py   (o run.bat en Windows)
"""
from __future__ import annotations

import logging
import os
import tempfile
import traceback
from pathlib import Path

import gradio as gr
from PIL import Image, ImageDraw

from pipeline import __version__, media
from pipeline.job import is_short, process_video, render_preview_frame, safe_stem, transcribe_video
from pipeline.layout import OUT_H, OUT_W, compute_short_layout, gameplay_from_rect
from pipeline.paths import OUTPUT_DIR, WORK_DIR, ensure_dirs
from pipeline.presets import get_path, list_presets, load_preset, save_preset, set_path
from pipeline.subtitles import STYLE_DEFAULTS
from pipeline.transcribe import Word

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ensure_dirs()

# (ruta en el preset, conversión al guardar). El orden define el orden de los inputs.
FIELDS: list[tuple[str, type]] = [
    ("mode", str),
    ("layout.type", str),
    ("layout.facecam_rect.0", float),
    ("layout.facecam_rect.1", float),
    ("layout.facecam_rect.2", float),
    ("layout.facecam_rect.3", float),
    ("layout.facecam_position", str),
    ("layout.facecam_width", float),
    ("layout.facecam_y", int),
    ("layout.facecam_radius", int),
    ("layout.facecam_shadow", bool),
    ("layout.gameplay_offset", float),
    ("layout.gameplay_offset_y", float),
    ("layout.gameplay_zoom", float),
    ("layout.split_ratio", float),
    ("layout.blur_strength", int),
    ("layout.blur_fg_y", int),
    ("toggles.subtitles", bool),
    ("toggles.hook_title", bool),
    ("toggles.highlight_keywords", bool),
    ("toggles.export_srt", bool),
    ("subtitles.style", str),
    ("subtitles.case", str),
    ("subtitles.strip_punctuation", bool),
    ("subtitles.font", str),
    ("subtitles.font_size", int),
    ("subtitles.primary_color", str),
    ("subtitles.outline_color", str),
    ("subtitles.highlight_color", str),
    ("subtitles.active_color", str),
    ("subtitles.outline", int),
    ("subtitles.shadow", int),
    ("subtitles.pos_y", int),
    ("subtitles.min_words", int),
    ("subtitles.max_words", int),
    ("subtitles.max_chars", int),
    ("subtitles.fade_ms", int),
    ("hook.text", str),
    ("hook.font", str),
    ("hook.font_size", int),
    ("hook.fill", str),
    ("hook.outline_color", str),
    ("hook.outer_color", str),
    ("hook.outline", int),
    ("hook.outer", int),
    ("hook.duration", float),
    ("hook.pos_y", int),
    ("hook.tilt", float),
    ("hook.uppercase", bool),
    ("transcription.model", str),
    ("transcription.device", str),
    ("transcription.glossary", str),
    ("transcription.reuse_cache", bool),
    ("audio.transcribe_track", int),
    ("audio.output_track", str),
    ("render.encoder", str),
    ("render.long_resolution", str),
    ("handle", str),
]
KEYS = [k for k, _ in FIELDS]
PCT_FIELDS = {f"layout.facecam_rect.{i}" for i in range(4)} | {"layout.facecam_width", "layout.split_ratio"}
STYLE_KEYS = ["subtitles.font", "subtitles.font_size", "subtitles.case", "subtitles.outline", "subtitles.shadow",
              "subtitles.min_words", "subtitles.max_words", "subtitles.max_chars", "subtitles.fade_ms"]
TABLE_HEADERS = ["palabra", "inicio", "fin", "resaltar"]


def _get(preset: dict, key: str):
    if key.startswith("layout.facecam_rect."):
        return preset["layout"]["facecam_rect"][int(key.rsplit(".", 1)[1])]
    return get_path(preset, key)


def preset_to_values(preset: dict) -> list:
    vals = []
    for key, _ in FIELDS:
        v = _get(preset, key)
        if key in PCT_FIELDS:
            v = round(float(v) * 100, 1)
        if key == "audio.output_track":
            v = str(v)
        vals.append(v)
    return vals


def values_to_preset(base_name: str, values: list) -> dict:
    preset = load_preset(base_name or "default")
    rect = list(preset["layout"]["facecam_rect"])
    for (key, conv), v in zip(FIELDS, values):
        if v is None:
            continue
        if key in PCT_FIELDS:
            v = float(v) / 100
        v = conv(v)
        if key.startswith("layout.facecam_rect."):
            rect[int(key.rsplit(".", 1)[1])] = v
        else:
            set_path(preset, key, v)
    preset["layout"]["facecam_rect"] = rect
    return preset


def _paths(files) -> list[str]:
    out = []
    for f in files or []:
        p = f if isinstance(f, str) else getattr(f, "name", None) or getattr(f, "path", None)
        if p:
            out.append(p)
    return out


# ---------------------------------------------------------------- tabla de revisión

def words_to_rows(words: list[Word]) -> list[list]:
    return [[w.text, round(w.start, 2), round(w.end, 2), bool(w.hl)] for w in words]


def _to_bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "sí", "si", "x", "yes")
    return bool(v)


def rows_to_words(rows) -> list[Word]:
    if hasattr(rows, "values") and hasattr(rows, "columns"):  # pandas.DataFrame
        rows = rows.values.tolist()
    words = []
    for r in rows or []:
        if not r or len(r) < 3:
            continue
        text = str(r[0] if r[0] is not None else "").strip()
        if not text:
            continue  # vaciar la palabra = borrarla
        try:
            start, end = float(r[1]), float(r[2])
        except (TypeError, ValueError):
            continue
        words.append(Word(text, start, max(end, start + 0.05), 1.0, _to_bool(r[3]) if len(r) > 3 else False))
    words.sort(key=lambda w: w.start)
    return words


# ---------------------------------------------------------------- callbacks

def on_load_preset(name: str):
    return preset_to_values(load_preset(name))


def on_save_preset(name: str, new_name: str, *values):
    target = (new_name or "").strip() or name
    preset = values_to_preset(name, list(values))
    path = save_preset(preset, target)
    return gr.update(choices=list_presets(), value=path.stem), f"Preset guardado: `{path.name}`"


def on_style_input(style: str):
    d = STYLE_DEFAULTS.get(style, {})
    return [gr.update(value=d[k.split(".", 1)[1]]) if k.split(".", 1)[1] in d else gr.update() for k in STYLE_KEYS]


def _preview(files, preset_name, t, values, review=None):
    paths = _paths(files)
    if not paths:
        raise gr.Error("Sube al menos un vídeo para ver la vista previa.")
    src = paths[0]
    preset = values_to_preset(preset_name, list(values))
    hook_text = (review or {}).get(src, {}).get("hook") or None
    tmp = Path(tempfile.mkdtemp(prefix="pv_", dir=WORK_DIR))
    try:
        info = media.probe(src)
        t = max(0.0, min(float(t), max(info.duration - 0.2, 0.0)))
        # Fotograma original con los rectángulos de gameplay (azul) y facecam (rojo).
        frame = media.extract_frame(src, tmp / "src.png", t)
        img = Image.open(frame).convert("RGB")
        if is_short(preset):
            lay = compute_short_layout(info.width, info.height, preset["layout"])
            d = ImageDraw.Draw(img)
            lw = max(3, info.width // 300)
            g = lay.gameplay
            d.rectangle([g.x, g.y, g.x + g.w - 1, g.y + g.h - 1], outline=(40, 140, 255), width=lw)
            if lay.facecam_src:
                f = lay.facecam_src
                d.rectangle([f.x, f.y, f.x + f.w - 1, f.y + f.h - 1], outline=(255, 40, 40), width=lw)
        img.thumbnail((960, 960))
        out = render_preview_frame(src, preset, t, tmp / "out.png", hook_text=hook_text)
        result = Image.open(out).convert("RGB")
        result.thumbnail((540, 960) if is_short(preset) else (960, 540))
        audio = ", ".join(
            f"{a['index']}: {a['title'] or a['codec']} ({a['channels']} ch)" for a in info.audio_tracks
        ) or "sin audio"
        msg = (
            f"`{Path(src).name}`: {info.width}x{info.height} @ {float(info.fps):.2f} fps, "
            f"{info.duration:.1f} s. Pistas de audio → {audio}"
        )
        return img, result, msg
    except gr.Error:
        raise
    except Exception as e:
        raise gr.Error(str(e)) from e


def on_preview(files, preset_name: str, t: float, review: dict, *values):
    return _preview(files, preset_name, t, values, review)


def on_image_click(evt: gr.SelectData, mark: str, first, img, files, preset_name, t, review, *values):
    """Marcar un rectángulo con dos clics (esquinas opuestas) sobre el fotograma original."""
    n_out = 7  # 4 de facecam + 3 de gameplay
    keep = [gr.update()] * n_out
    if mark == "nada" or img is None:
        return [first, *keep, gr.update(), gr.update(), gr.update()]
    w, h = img.size if hasattr(img, "size") else (img.shape[1], img.shape[0])
    x, y = evt.index[0] / w, evt.index[1] / h
    if first is None:
        marked = img.copy()
        d = ImageDraw.Draw(marked)
        px, py = evt.index
        d.ellipse([px - 7, py - 7, px + 7, py + 7], fill=(255, 220, 0), outline=(0, 0, 0), width=2)
        return [(x, y), *keep, marked, gr.update(), "Primera esquina marcada: haz clic en la esquina opuesta."]

    x0, x1 = sorted((first[0], x))
    y0, y1 = sorted((first[1], y))
    rect = [x0, y0, max(x1 - x0, 0.01), max(y1 - y0, 0.01)]
    vals = list(values)
    idx = {k: i for i, k in enumerate(KEYS)}
    upd = list(keep)
    if mark == "facecam":
        for i in range(4):
            v = round(rect[i] * 100, 1)
            vals[idx[f"layout.facecam_rect.{i}"]] = v
            upd[i] = gr.update(value=v)
    else:  # gameplay
        info = media.probe(_paths(files)[0])
        kind = vals[idx["layout.type"]]
        if kind == "blur":
            aspect = info.width / info.height
        elif kind == "split":
            top = OUT_H * float(vals[idx["layout.split_ratio"]]) / 100
            aspect = OUT_W / (OUT_H - top)
        else:
            aspect = OUT_W / OUT_H
        ox, oy, zoom = gameplay_from_rect(info.width, info.height, rect, aspect)
        for j, (k, v) in enumerate(zip(["layout.gameplay_offset", "layout.gameplay_offset_y", "layout.gameplay_zoom"],
                                       [ox, oy, zoom])):
            vals[idx[k]] = v
            upd[4 + j] = gr.update(value=v)
    src_img, out_img, _ = _preview(files, preset_name, t, vals, review)
    return [None, *upd, src_img, out_img, f"Rectángulo de {mark} actualizado."]


def on_autodetect(files):
    paths = _paths(files)
    if not paths:
        raise gr.Error("Sube un vídeo primero.")
    from pipeline.facedetect import DetectorUnavailable, detect_facecam

    try:
        rect = detect_facecam(paths[0])
    except DetectorUnavailable as e:
        raise gr.Error(str(e)) from e
    if rect is None:
        raise gr.Error("No se ha encontrado ninguna cara. Marca la facecam con dos clics sobre el fotograma.")
    return [gr.update(value=round(v * 100, 1)) for v in rect] + ["Facecam detectada (revísala en la vista previa)."]


def on_transcribe(files, review: dict, preset_name: str, *values, progress=gr.Progress()):
    paths = _paths(files)
    if not paths:
        raise gr.Error("Sube uno o varios vídeos.")
    preset = values_to_preset(preset_name, list(values))
    review = dict(review or {})
    errors = []
    for i, src in enumerate(paths):
        name = Path(src).name

        def report(frac: float, msg: str, i=i, name=name):
            progress((i + frac) / len(paths), desc=f"[{i + 1}/{len(paths)}] {name}: {msg}")

        try:
            words = transcribe_video(src, preset, report)
            review[src] = {"rows": words_to_rows(words), "hook": preset["hook"].get("text", "")}
        except Exception as e:
            logging.exception("Error transcribiendo %s", src)
            errors.append(f"{name}: {e}")
    choices = [(Path(p).name, p) for p in paths if p in review]
    if not choices:
        raise gr.Error("No se pudo transcribir ningún vídeo:\n" + "\n".join(errors))
    first = choices[0][1]
    msg = f"{len(choices)} vídeo(s) listos para revisar." + ("\n\nErrores:\n" + "\n".join(errors) if errors else "")
    return (review, gr.update(choices=choices, value=first), review[first]["rows"], review[first]["hook"], msg)


def on_select_video(path, review: dict):
    entry = (review or {}).get(path)
    if not entry:
        return gr.update(), gr.update()
    return entry["rows"], entry["hook"]


def on_table_change(rows, path, review: dict):
    if path and path in (review or {}):
        if hasattr(rows, "values") and hasattr(rows, "columns"):
            rows = rows.values.tolist()
        review[path]["rows"] = rows
    return review


def on_hook_change(text, path, review: dict):
    if path and path in (review or {}):
        review[path]["hook"] = text
    return review


def on_process(files, review: dict, preset_name: str, *values, progress=gr.Progress()):
    paths = _paths(files)
    if not paths:
        raise gr.Error("Sube uno o varios vídeos.")
    preset = values_to_preset(preset_name, list(values))
    review = review or {}
    log: list[str] = []
    outputs: list[str] = []
    last_video = None
    n = len(paths)
    used: dict[str, int] = {}
    for i, src in enumerate(paths):
        stem = safe_stem(src)
        used[stem] = used.get(stem, 0) + 1
        if used[stem] > 1:  # mismo nombre en el lote: no sobrescribir
            stem = f"{stem}_{used[stem]}"
        name = Path(src).name
        entry = review.get(src)
        log.append(f"▶ [{i + 1}/{n}] {name}" + ("  (transcripción revisada)" if entry else ""))
        yield "\n".join(log), outputs, last_video

        def report(frac: float, msg: str, i=i, name=name):
            progress((i + frac) / n, desc=f"[{i + 1}/{n}] {name}: {msg}")

        try:
            if entry:
                res = process_video(src, preset, progress=report, stem=stem, words=rows_to_words(entry["rows"]),
                                    reviewed=True, hook_text=entry.get("hook") or "")
            else:
                res = process_video(src, preset, progress=report, stem=stem)
            outputs.extend(str(p) for p in res.files)
            last_video = str(res.video)
            log.append(res.summary)
        except Exception as e:
            logging.exception("Error procesando %s", src)
            log.append(f"✖ Error en {name}: {e}\n{traceback.format_exc(limit=2)}")
        yield "\n".join(log), outputs, last_video
    log.append(f"Terminado. Archivos en: {OUTPUT_DIR}")
    yield "\n".join(log), outputs, last_video


# ---------------------------------------------------------------- UI

def build_ui() -> gr.Blocks:
    names = list_presets() or ["default"]
    first = "gta" if "gta" in names else names[0]
    v = dict(zip(KEYS, preset_to_values(load_preset(first))))
    comps: dict[str, gr.components.Component] = {}

    def color(key, label):
        comps[key] = gr.ColorPicker(v[key], label=label)

    with gr.Blocks(title="ClipForge") as demo:
        review_state = gr.State({})
        click_state = gr.State(None)
        gr.Markdown(f"# 🎮 ClipForge `v{__version__}`")

        with gr.Row():
            with gr.Column(scale=3):
                files = gr.File(label="Vídeos (arrastra uno o varios)", file_count="multiple",
                                file_types=["video"], type="filepath")
            with gr.Column(scale=2):
                preset_dd = gr.Dropdown(names, value=first, label="Preset")
                comps["mode"] = gr.Radio([("Short 9:16 (TikTok/Reels/Shorts)", "short"), ("Largo 16:9 (YouTube)", "largo")],
                                         value=v["mode"], label="Modo")
                with gr.Row():
                    new_name = gr.Textbox(label="Guardar como (vacío = sobrescribir)", scale=3)
                    save_btn = gr.Button("💾 Guardar preset", scale=1)
                preset_msg = gr.Markdown()

        with gr.Row():
            with gr.Column(scale=3):
                with gr.Tab("Interruptores"):
                    with gr.Row():
                        comps["toggles.subtitles"] = gr.Checkbox(v["toggles.subtitles"], label="Subtítulos")
                        comps["toggles.hook_title"] = gr.Checkbox(v["toggles.hook_title"], label="Título gancho (solo Short)")
                        comps["toggles.highlight_keywords"] = gr.Checkbox(v["toggles.highlight_keywords"], label="Resaltar palabras clave")
                        comps["toggles.export_srt"] = gr.Checkbox(v["toggles.export_srt"], label="Exportar .srt aparte")
                    gr.Markdown("Corte de silencios, emojis, auto-zoom, efectos de sonido, barra de progreso, "
                                "marca de agua, Demucs y LLM llegan en la Fase 3.")

                with gr.Tab("Layout"):
                    comps["layout.type"] = gr.Radio(
                        [("Facecam esquina", "facecam_esquina"), ("Split (facecam arriba)", "split"),
                         ("Blur (16:9 + fondo desenfocado)", "blur"), ("Sin facecam", "sin_facecam")],
                        value=v["layout.type"], label="Layout del Short")
                    gr.Markdown("**Facecam en el vídeo original** (% del ancho/alto · recuadro rojo). "
                                "También puedes marcarla con dos clics sobre el fotograma →")
                    with gr.Row():
                        comps["layout.facecam_rect.0"] = gr.Slider(0, 100, v["layout.facecam_rect.0"], step=0.5, label="X %")
                        comps["layout.facecam_rect.1"] = gr.Slider(0, 100, v["layout.facecam_rect.1"], step=0.5, label="Y %")
                        comps["layout.facecam_rect.2"] = gr.Slider(1, 100, v["layout.facecam_rect.2"], step=0.5, label="Ancho %")
                        comps["layout.facecam_rect.3"] = gr.Slider(1, 100, v["layout.facecam_rect.3"], step=0.5, label="Alto %")
                    autodetect_btn = gr.Button("🔎 Autodetectar facecam", size="sm")
                    gr.Markdown("**Facecam en el Short** (facecam esquina)")
                    with gr.Row():
                        comps["layout.facecam_position"] = gr.Dropdown(
                            [("Arriba derecha", "top-right"), ("Arriba izquierda", "top-left"), ("Arriba centro", "top-center")],
                            value=v["layout.facecam_position"], label="Posición")
                        comps["layout.facecam_width"] = gr.Slider(20, 100, v["layout.facecam_width"], step=1, label="Tamaño (% ancho)")
                        comps["layout.facecam_y"] = gr.Slider(0, 1200, v["layout.facecam_y"], step=10, label="Y (px)")
                    with gr.Row():
                        comps["layout.facecam_radius"] = gr.Slider(0, 80, v["layout.facecam_radius"], step=2, label="Redondeo esquinas (px)")
                        comps["layout.facecam_shadow"] = gr.Checkbox(v["layout.facecam_shadow"], label="Sombra suave")
                    gr.Markdown("**Gameplay** (recuadro azul)")
                    with gr.Row():
                        comps["layout.gameplay_offset"] = gr.Slider(-1, 1, v["layout.gameplay_offset"], step=0.05, label="Desplazamiento X (-1 izq · 1 der)")
                        comps["layout.gameplay_offset_y"] = gr.Slider(-1, 1, v["layout.gameplay_offset_y"], step=0.05, label="Desplazamiento Y (con zoom)")
                        comps["layout.gameplay_zoom"] = gr.Slider(1, 3, v["layout.gameplay_zoom"], step=0.05, label="Zoom")
                    with gr.Row():
                        comps["layout.split_ratio"] = gr.Slider(15, 70, v["layout.split_ratio"], step=1, label="Split: alto de la facecam (%)")
                        comps["layout.blur_strength"] = gr.Slider(0, 60, v["layout.blur_strength"], step=1, label="Blur: desenfoque")
                        comps["layout.blur_fg_y"] = gr.Slider(300, 1620, v["layout.blur_fg_y"], step=10, label="Blur: centro Y del gameplay")

                with gr.Tab("Subtítulos"):
                    with gr.Row():
                        comps["subtitles.style"] = gr.Radio(
                            [("Frase", "frase"), ("Resaltado", "resaltado"), ("Karaoke", "karaoke")],
                            value=v["subtitles.style"], label="Estilo", info="Al cambiarlo se cargan sus valores típicos")
                        comps["subtitles.case"] = gr.Radio(
                            [("Original", "original"), ("minúsculas", "minusculas"), ("MAYÚSCULAS", "mayusculas")],
                            value=v["subtitles.case"], label="Mayúsculas")
                        comps["subtitles.strip_punctuation"] = gr.Checkbox(v["subtitles.strip_punctuation"], label="Quitar puntos y comas")
                    with gr.Row():
                        comps["subtitles.font"] = gr.Textbox(v["subtitles.font"], label="Fuente (familia, en assets/fonts)")
                        comps["subtitles.font_size"] = gr.Slider(30, 160, v["subtitles.font_size"], step=2, label="Tamaño")
                        comps["subtitles.pos_y"] = gr.Slider(150, 1260, v["subtitles.pos_y"], step=10, label="Posición Y (centro)",
                                                             info="Short: nunca en los 660 px de abajo")
                    with gr.Row():
                        color("subtitles.primary_color", "Texto")
                        color("subtitles.outline_color", "Contorno")
                        color("subtitles.highlight_color", "Palabras clave")
                        color("subtitles.active_color", "Karaoke: palabra actual")
                    with gr.Row():
                        comps["subtitles.outline"] = gr.Slider(0, 12, v["subtitles.outline"], step=1, label="Grosor contorno")
                        comps["subtitles.shadow"] = gr.Slider(0, 12, v["subtitles.shadow"], step=1, label="Sombra")
                        comps["subtitles.fade_ms"] = gr.Slider(0, 300, v["subtitles.fade_ms"], step=10, label="Fundido (ms)")
                    with gr.Row():
                        comps["subtitles.min_words"] = gr.Slider(1, 6, v["subtitles.min_words"], step=1, label="Mín. palabras")
                        comps["subtitles.max_words"] = gr.Slider(1, 10, v["subtitles.max_words"], step=1, label="Máx. palabras")
                        comps["subtitles.max_chars"] = gr.Slider(8, 60, v["subtitles.max_chars"], step=1, label="Máx. caracteres")

                with gr.Tab("Título gancho"):
                    comps["hook.text"] = gr.Textbox(v["hook.text"], label="Texto por defecto (cada vídeo puede tener el suyo en «Revisar»)",
                                                    placeholder="UNA TRAGEDIA  ·  usa | para partir en dos líneas")
                    with gr.Row():
                        comps["hook.font"] = gr.Textbox(v["hook.font"], label="Fuente (se usa en cursiva)")
                        comps["hook.font_size"] = gr.Slider(50, 220, v["hook.font_size"], step=2, label="Tamaño")
                        comps["hook.uppercase"] = gr.Checkbox(v["hook.uppercase"], label="MAYÚSCULAS")
                    with gr.Row():
                        color("hook.fill", "Relleno")
                        color("hook.outline_color", "Contorno")
                        color("hook.outer_color", "Contorno exterior")
                    with gr.Row():
                        comps["hook.outline"] = gr.Slider(0, 30, v["hook.outline"], step=1, label="Grosor contorno")
                        comps["hook.outer"] = gr.Slider(0, 30, v["hook.outer"], step=1, label="Grosor exterior")
                        comps["hook.tilt"] = gr.Slider(-15, 15, v["hook.tilt"], step=1, label="Giro (°)")
                    with gr.Row():
                        comps["hook.duration"] = gr.Slider(0, 10, v["hook.duration"], step=0.5, label="Duración (s) · 0 = todo el clip")
                        comps["hook.pos_y"] = gr.Slider(0, 1260, v["hook.pos_y"], step=10, label="Posición Y · 0 = auto (debajo de la facecam)")

                with gr.Tab("Transcripción y audio"):
                    with gr.Row():
                        comps["transcription.model"] = gr.Dropdown(["large-v3-turbo", "large-v3", "medium", "small"],
                                                                   value=v["transcription.model"], label="Modelo Whisper")
                        comps["transcription.device"] = gr.Dropdown([("Auto", "auto"), ("GPU (CUDA)", "cuda"), ("CPU", "cpu")],
                                                                    value=v["transcription.device"], label="Dispositivo")
                        comps["transcription.reuse_cache"] = gr.Checkbox(v["transcription.reuse_cache"], label="Reusar transcripción si ya existe")
                    comps["transcription.glossary"] = gr.Textbox(v["transcription.glossary"], lines=2,
                                                                 label="Glosario del juego (nombres propios, jerga; también se resaltan)")
                    with gr.Row():
                        comps["audio.transcribe_track"] = gr.Number(v["audio.transcribe_track"], precision=0,
                                                                    label="Pista del micro (para transcribir)")
                        comps["audio.output_track"] = gr.Dropdown(["0", "1", "2", "3", "mix"], value=v["audio.output_track"],
                                                                  label="Pista de audio del vídeo final", allow_custom_value=True)
                    with gr.Row():
                        comps["render.encoder"] = gr.Dropdown([("Auto (NVENC si hay NVIDIA)", "auto"), ("NVENC", "nvenc"), ("CPU x264", "x264")],
                                                              value=v["render.encoder"], label="Encoder")
                        comps["render.long_resolution"] = gr.Dropdown([("Original", "original"), ("1080p", "1080p")],
                                                                      value=v["render.long_resolution"], label="Resolución Modo Largo")
                        comps["handle"] = gr.Textbox(v["handle"], label="@handle (marca de agua: Fase 3)")

                with gr.Tab("Revisar transcripción"):
                    gr.Markdown("1) **Transcribir** · 2) corrige palabras/tiempos y marca qué resaltar "
                                "(vacía una palabra para borrarla) · 3) escribe el gancho · 4) **Procesar**. "
                                "Los vídeos sin revisar se transcriben al procesar.")
                    with gr.Row():
                        transcribe_btn = gr.Button("📝 Transcribir para revisar", variant="secondary")
                        video_dd = gr.Dropdown([], label="Vídeo", interactive=True)
                    hook_box = gr.Textbox(label="Título gancho de este vídeo", placeholder="UNA TRAGEDIA")
                    review_msg = gr.Markdown()
                    table = gr.Dataframe(headers=TABLE_HEADERS, datatype=["str", "number", "number", "bool"],
                                         type="array", interactive=True, column_count=(4, "fixed"),
                                         label="Transcripción (tiempos en segundos)", max_height=520)

            with gr.Column(scale=2):
                with gr.Row():
                    t_slider = gr.Slider(0, 600, 5, step=0.5, label="Segundo para la vista previa", scale=3)
                    mark_mode = gr.Radio([("No marcar", "nada"), ("Marcar facecam", "facecam"), ("Marcar gameplay", "gameplay")],
                                         value="nada", label="Clic sobre el original", scale=2)
                preview_btn = gr.Button("👁 Vista previa")
                preview_info = gr.Markdown()
                with gr.Row():
                    src_img = gr.Image(label="Original (rojo = facecam, azul = gameplay)", type="pil", interactive=False)
                    out_img = gr.Image(label="Resultado", type="pil", interactive=False)

        process_btn = gr.Button("🚀 Procesar todos", variant="primary", size="lg")
        with gr.Row():
            log_box = gr.Textbox(label="Registro y tiempos por paso", lines=16, max_lines=40)
            with gr.Column():
                video_out = gr.Video(label="Último resultado")
                files_out = gr.Files(label="Archivos generados")

        inputs = [comps[k] for k in KEYS]
        rect_sliders = [comps[f"layout.facecam_rect.{i}"] for i in range(4)]
        gp_sliders = [comps["layout.gameplay_offset"], comps["layout.gameplay_offset_y"], comps["layout.gameplay_zoom"]]
        preview_out = [src_img, out_img, preview_info]

        preset_dd.change(on_load_preset, preset_dd, inputs)
        save_btn.click(on_save_preset, [preset_dd, new_name, *inputs], [preset_dd, preset_msg])
        comps["subtitles.style"].input(on_style_input, comps["subtitles.style"], [comps[k] for k in STYLE_KEYS])
        preview_btn.click(on_preview, [files, preset_dd, t_slider, review_state, *inputs], preview_out)
        mark_mode.change(lambda: None, None, click_state)
        src_img.select(on_image_click,
                       [mark_mode, click_state, src_img, files, preset_dd, t_slider, review_state, *inputs],
                       [click_state, *rect_sliders, *gp_sliders, src_img, out_img, preview_info])
        autodetect_btn.click(on_autodetect, files, [*rect_sliders, preview_info]).success(
            on_preview, [files, preset_dd, t_slider, review_state, *inputs], preview_out)
        transcribe_btn.click(on_transcribe, [files, review_state, preset_dd, *inputs],
                             [review_state, video_dd, table, hook_box, review_msg])
        video_dd.change(on_select_video, [video_dd, review_state], [table, hook_box])
        table.change(on_table_change, [table, video_dd, review_state], review_state)
        hook_box.change(on_hook_change, [hook_box, video_dd, review_state], review_state)
        process_btn.click(on_process, [files, review_state, preset_dd, *inputs], [log_box, files_out, video_out])
    return demo


if __name__ == "__main__":
    port = int(os.environ.get("CLIPFORGE_PORT", "7860"))
    build_ui().queue().launch(
        server_name="127.0.0.1",
        server_port=port,
        inbrowser=os.environ.get("CLIPFORGE_NO_BROWSER") != "1",
        allowed_paths=[str(OUTPUT_DIR)],
    )
