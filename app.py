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
from pipeline.job import process_video, render_preview_frame, safe_stem
from pipeline.layout import compute_short_layout
from pipeline.paths import OUTPUT_DIR, WORK_DIR, ensure_dirs
from pipeline.presets import get_path, list_presets, load_preset, save_preset, set_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ensure_dirs()

# (ruta en el preset, conversión al guardar). El orden define el orden de los inputs.
FIELDS: list[tuple[str, type]] = [
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
    ("layout.gameplay_zoom", float),
    ("toggles.subtitles", bool),
    ("subtitles.case", str),
    ("subtitles.strip_punctuation", bool),
    ("subtitles.font", str),
    ("subtitles.font_size", int),
    ("subtitles.primary_color", str),
    ("subtitles.outline_color", str),
    ("subtitles.outline", int),
    ("subtitles.shadow", int),
    ("subtitles.pos_y", int),
    ("subtitles.min_words", int),
    ("subtitles.max_words", int),
    ("subtitles.max_chars", int),
    ("toggles.export_srt", bool),
    ("transcription.model", str),
    ("transcription.device", str),
    ("transcription.glossary", str),
    ("transcription.reuse_cache", bool),
    ("audio.transcribe_track", int),
    ("audio.output_track", str),
    ("render.encoder", str),
    ("handle", str),
]
PCT_FIELDS = {f"layout.facecam_rect.{i}" for i in range(4)} | {"layout.facecam_width"}


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


# ---------------------------------------------------------------- callbacks

def on_load_preset(name: str):
    preset = load_preset(name)
    return preset_to_values(preset)


def on_save_preset(name: str, new_name: str, *values):
    target = (new_name or "").strip() or name
    preset = values_to_preset(name, list(values))
    path = save_preset(preset, target)
    names = list_presets()
    return gr.update(choices=names, value=path.stem), f"Preset guardado: {path.name}"


def _first_file(files) -> str | None:
    if not files:
        return None
    f = files[0]
    return f if isinstance(f, str) else getattr(f, "name", None) or getattr(f, "path", None)


def on_preview(files, preset_name: str, t: float, *values):
    src = _first_file(files)
    if not src:
        raise gr.Error("Sube al menos un vídeo para ver la vista previa.")
    preset = values_to_preset(preset_name, list(values))
    tmp = Path(tempfile.mkdtemp(prefix="pv_", dir=WORK_DIR))
    try:
        info = media.probe(src)
        t = max(0.0, min(float(t), max(info.duration - 0.2, 0.0)))
        # Fotograma original con los rectángulos de gameplay (azul) y facecam (rojo).
        frame = media.extract_frame(src, tmp / "src.png", t)
        img = Image.open(frame).convert("RGB")
        lay = compute_short_layout(info.width, info.height, preset["layout"])
        d = ImageDraw.Draw(img)
        lw = max(3, info.width // 300)
        g = lay.gameplay
        d.rectangle([g.x, g.y, g.x + g.w - 1, g.y + g.h - 1], outline=(40, 140, 255), width=lw)
        if lay.facecam_src:
            f = lay.facecam_src
            d.rectangle([f.x, f.y, f.x + f.w - 1, f.y + f.h - 1], outline=(255, 40, 40), width=lw)
        img.thumbnail((960, 960))
        out = render_preview_frame(src, preset, t, tmp / "out.png")
        result = Image.open(out).convert("RGB")
        result.thumbnail((540, 960))
        audio = ", ".join(
            f"{a['index']}: {a['title'] or a['codec']} ({a['channels']} ch)" for a in info.audio_tracks
        ) or "sin audio"
        msg = (
            f"{Path(src).name}: {info.width}x{info.height} @ {float(info.fps):.2f} fps, "
            f"{info.duration:.1f} s. Pistas de audio → {audio}"
        )
        return img, result, msg
    except Exception as e:
        raise gr.Error(str(e)) from e


def on_process(files, preset_name: str, *values, progress=gr.Progress()):
    if not files:
        raise gr.Error("Sube uno o varios vídeos.")
    preset = values_to_preset(preset_name, list(values))
    paths = [f if isinstance(f, str) else getattr(f, "name", None) or getattr(f, "path", None) for f in files]
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
        log.append(f"▶ [{i + 1}/{n}] {name}")
        yield "\n".join(log), outputs, last_video

        def report(frac: float, msg: str, i=i, name=name):
            progress((i + frac) / n, desc=f"[{i + 1}/{n}] {name}: {msg}")

        try:
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
    p = load_preset(first)
    v = dict(zip([k for k, _ in FIELDS], preset_to_values(p)))

    with gr.Blocks(title="ClipForge") as demo:
        gr.Markdown(f"# 🎮 ClipForge `v{__version__}` — Fase 1 (MVP: Modo Short · Facecam esquina · estilo Frase)")

        with gr.Row():
            with gr.Column(scale=3):
                files = gr.File(label="Vídeos (arrastra uno o varios)", file_count="multiple",
                                file_types=["video"], type="filepath")
            with gr.Column(scale=2):
                preset_dd = gr.Dropdown(names, value=first, label="Preset")
                gr.Radio(["Short (9:16)"], value="Short (9:16)", label="Modo",
                                info="Modo Largo (16:9 + .srt): Fase 2")
                with gr.Row():
                    new_name = gr.Textbox(label="Guardar como (vacío = sobrescribir)", scale=3)
                    save_btn = gr.Button("💾 Guardar preset", scale=1)
                preset_msg = gr.Markdown()

        comps: dict[str, gr.components.Component] = {}
        with gr.Row():
            with gr.Column(scale=3):
                with gr.Tab("Layout"):
                    comps["layout.type"] = gr.Radio(
                        [("Facecam esquina", "facecam_esquina"), ("Sin facecam", "sin_facecam")],
                        value=v["layout.type"], label="Layout")
                    gr.Markdown("**Facecam en el vídeo original** (% del ancho/alto; recuadro rojo en la vista previa)")
                    with gr.Row():
                        comps["layout.facecam_rect.0"] = gr.Slider(0, 100, v["layout.facecam_rect.0"], step=0.5, label="X %")
                        comps["layout.facecam_rect.1"] = gr.Slider(0, 100, v["layout.facecam_rect.1"], step=0.5, label="Y %")
                        comps["layout.facecam_rect.2"] = gr.Slider(1, 100, v["layout.facecam_rect.2"], step=0.5, label="Ancho %")
                        comps["layout.facecam_rect.3"] = gr.Slider(1, 100, v["layout.facecam_rect.3"], step=0.5, label="Alto %")
                    gr.Markdown("**Facecam en el Short**")
                    with gr.Row():
                        comps["layout.facecam_position"] = gr.Dropdown(
                            [("Arriba derecha", "top-right"), ("Arriba izquierda", "top-left"), ("Arriba centro", "top-center")],
                            value=v["layout.facecam_position"], label="Posición")
                        comps["layout.facecam_width"] = gr.Slider(20, 100, v["layout.facecam_width"], step=1, label="Tamaño (% ancho)")
                        comps["layout.facecam_y"] = gr.Slider(0, 1200, v["layout.facecam_y"], step=10, label="Y (px)")
                    with gr.Row():
                        comps["layout.facecam_radius"] = gr.Slider(0, 80, v["layout.facecam_radius"], step=2, label="Redondeo esquinas (px)")
                        comps["layout.facecam_shadow"] = gr.Checkbox(v["layout.facecam_shadow"], label="Sombra suave")
                    gr.Markdown("**Gameplay** (recorte 9:16, recuadro azul en la vista previa)")
                    with gr.Row():
                        comps["layout.gameplay_offset"] = gr.Slider(-1, 1, v["layout.gameplay_offset"], step=0.05, label="Desplazamiento horizontal (-1 izq · 1 der)")
                        comps["layout.gameplay_zoom"] = gr.Slider(1, 2.5, v["layout.gameplay_zoom"], step=0.05, label="Zoom")

                with gr.Tab("Subtítulos"):
                    with gr.Row():
                        comps["toggles.subtitles"] = gr.Checkbox(v["toggles.subtitles"], label="Subtítulos quemados")
                        comps["toggles.export_srt"] = gr.Checkbox(v["toggles.export_srt"], label="Exportar .srt aparte")
                        gr.Radio(["Frase"], value="Frase", label="Estilo", info="Resaltado, Karaoke y Título gancho: Fase 2")
                    with gr.Row():
                        comps["subtitles.case"] = gr.Radio(
                            [("Original", "original"), ("minúsculas", "minusculas"), ("MAYÚSCULAS", "mayusculas")],
                            value=v["subtitles.case"], label="Mayúsculas")
                        comps["subtitles.strip_punctuation"] = gr.Checkbox(v["subtitles.strip_punctuation"], label="Quitar puntos y comas")
                    with gr.Row():
                        comps["subtitles.font"] = gr.Textbox(v["subtitles.font"], label="Fuente (nombre de familia, en assets/fonts)")
                        comps["subtitles.font_size"] = gr.Slider(30, 160, v["subtitles.font_size"], step=2, label="Tamaño")
                        comps["subtitles.pos_y"] = gr.Slider(200, 1260, v["subtitles.pos_y"], step=10, label="Posición Y (px, centro)",
                                                             info="Nunca entra en los 660 px de abajo")
                    with gr.Row():
                        comps["subtitles.primary_color"] = gr.ColorPicker(v["subtitles.primary_color"], label="Color texto")
                        comps["subtitles.outline_color"] = gr.ColorPicker(v["subtitles.outline_color"], label="Color contorno")
                        comps["subtitles.outline"] = gr.Slider(0, 12, v["subtitles.outline"], step=1, label="Grosor contorno")
                        comps["subtitles.shadow"] = gr.Slider(0, 12, v["subtitles.shadow"], step=1, label="Sombra")
                    with gr.Row():
                        comps["subtitles.min_words"] = gr.Slider(1, 6, v["subtitles.min_words"], step=1, label="Mín. palabras/frase")
                        comps["subtitles.max_words"] = gr.Slider(1, 10, v["subtitles.max_words"], step=1, label="Máx. palabras/frase")
                        comps["subtitles.max_chars"] = gr.Slider(8, 60, v["subtitles.max_chars"], step=1, label="Máx. caracteres/frase")

                with gr.Tab("Transcripción y audio"):
                    with gr.Row():
                        comps["transcription.model"] = gr.Dropdown(["large-v3-turbo", "large-v3", "medium", "small"],
                                                                   value=v["transcription.model"], label="Modelo Whisper")
                        comps["transcription.device"] = gr.Dropdown([("Auto", "auto"), ("GPU (CUDA)", "cuda"), ("CPU", "cpu")],
                                                                    value=v["transcription.device"], label="Dispositivo")
                        comps["transcription.reuse_cache"] = gr.Checkbox(v["transcription.reuse_cache"], label="Reusar transcripción si ya existe")
                    comps["transcription.glossary"] = gr.Textbox(v["transcription.glossary"], lines=2,
                                                                 label="Glosario del juego (nombres propios, jerga)")
                    with gr.Row():
                        comps["audio.transcribe_track"] = gr.Number(v["audio.transcribe_track"], precision=0,
                                                                    label="Pista del micro (para transcribir)")
                        comps["audio.output_track"] = gr.Dropdown(["0", "1", "2", "3", "mix"], value=v["audio.output_track"],
                                                                  label="Pista de audio del vídeo final", allow_custom_value=True)
                        comps["render.encoder"] = gr.Dropdown([("Auto (NVENC si hay NVIDIA)", "auto"), ("NVENC", "nvenc"), ("CPU x264", "x264")],
                                                              value=v["render.encoder"], label="Encoder")
                        comps["handle"] = gr.Textbox(v["handle"], label="@handle (marca de agua: Fase 3)")

            with gr.Column(scale=2):
                t_slider = gr.Slider(0, 600, 5, step=0.5, label="Segundo para la vista previa")
                preview_btn = gr.Button("👁 Vista previa")
                preview_info = gr.Markdown()
                with gr.Row():
                    src_img = gr.Image(label="Original (rojo = facecam, azul = gameplay)", type="pil")
                    out_img = gr.Image(label="Resultado", type="pil")

        process_btn = gr.Button("🚀 Procesar todos", variant="primary", size="lg")
        with gr.Row():
            log_box = gr.Textbox(label="Registro y tiempos por paso", lines=16, max_lines=40)
            with gr.Column():
                video_out = gr.Video(label="Último resultado")
                files_out = gr.Files(label="Archivos generados")

        inputs = [comps[k] for k, _ in FIELDS]
        preset_dd.change(on_load_preset, preset_dd, inputs)
        save_btn.click(on_save_preset, [preset_dd, new_name, *inputs], [preset_dd, preset_msg])
        preview_btn.click(on_preview, [files, preset_dd, t_slider, *inputs], [src_img, out_img, preview_info])
        process_btn.click(on_process, [files, preset_dd, *inputs], [log_box, files_out, video_out])
    return demo


if __name__ == "__main__":
    port = int(os.environ.get("CLIPFORGE_PORT", "7860"))
    build_ui().queue().launch(
        server_name="127.0.0.1",
        server_port=port,
        inbrowser=os.environ.get("CLIPFORGE_NO_BROWSER") != "1",
        allowed_paths=[str(OUTPUT_DIR)],
    )
