"""ClipForge — interfaz Gradio (localhost).

Ejecuta:  python app.py   (o run.bat en Windows)
"""
from __future__ import annotations

import csv
import logging
import os
import tempfile
import time
import traceback
from pathlib import Path

import gradio as gr
from PIL import Image, ImageDraw

from pipeline import __version__, llm, media, remotion_render, sfx
from pipeline.job import (enrich_with_llm, is_short, llm_enabled, process_video, render_preview_frame,
                          safe_stem, transcribe_video)
from pipeline.layout import OUT_H, OUT_W, compute_short_layout, gameplay_from_rect
from pipeline.paths import OUTPUT_DIR, WORK_DIR, ensure_dirs
from pipeline.phrases import apply_phrase_rows, assign_groups, phrase_rows
from pipeline.presets import get_path, list_presets, load_preset, save_preset, set_path
from pipeline.subtitles import STYLE_DEFAULTS, WATERMARK_POSITIONS
from pipeline.transcribe import Word, words_from_json, words_to_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ensure_dirs()

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".ts"}

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
    ("toggles.cut_silences", bool),
    ("toggles.auto_zoom", bool),
    ("toggles.sfx", bool),
    ("toggles.progress_bar", bool),
    ("toggles.watermark", bool),
    ("toggles.isolate_voice", bool),
    ("toggles.llm_correction", bool),
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
    ("silences.source", str),
    ("silences.threshold_db", float),
    ("silences.min_duration", float),
    ("silences.padding", float),
    ("zoom.on_keywords", bool),
    ("zoom.on_shouts", bool),
    ("zoom.amount", float),
    ("zoom.shout_db", float),
    ("zoom.min_gap", float),
    ("sfx.on_zoom", str),
    ("sfx.on_hook", str),
    ("sfx.on_cut", str),
    ("sfx.volume", float),
    ("progress_bar.color", str),
    ("progress_bar.height", int),
    ("progress_bar.position", str),
    ("watermark.position", str),
    ("watermark.opacity", int),
    ("watermark.font_size", int),
    ("watermark.color", str),
    ("llm.provider", str),
    ("llm.ollama_model", str),
    ("llm.claude_model", str),
    ("llm.openai_model", str),
    ("llm.correct_text", bool),
    ("llm.pick_keywords", bool),
    ("llm.propose_hook", bool),
    ("llm.metadata", bool),
    ("transcription.model", str),
    ("transcription.device", str),
    ("transcription.glossary", str),
    ("transcription.reuse_cache", bool),
    ("audio.transcribe_track", int),
    ("audio.output_track", str),
    ("render.encoder", str),
    ("render.long_resolution", str),
    ("render.engine", str),
    ("subtitles.animation", str),
    ("handle", str),
]
KEYS = [k for k, _ in FIELDS]
PCT_FIELDS = {f"layout.facecam_rect.{i}" for i in range(4)} | {"layout.facecam_width", "layout.split_ratio"}
STYLE_KEYS = ["subtitles.font", "subtitles.font_size", "subtitles.case", "subtitles.outline", "subtitles.shadow",
              "subtitles.min_words", "subtitles.max_words", "subtitles.max_chars", "subtitles.fade_ms"]
WORD_HEADERS = ["palabra", "inicio", "fin", "resaltar", "frase"]
PHRASE_HEADERS = ["inicio", "fin", "texto del subtítulo"]
NO_SFX = "(ninguno)"


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
        if key.startswith("sfx.on_"):
            v = v or NO_SFX
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
        if key.startswith("sfx.on_") and v == NO_SFX:
            v = ""
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


def gather_sources(files, folder: str = "", recursive: bool = False) -> list[str]:
    """Vídeos subidos + vídeos de una carpeta (procesado por lotes)."""
    out = _paths(files)
    folder = (folder or "").strip().strip('"')
    if folder:
        base = Path(folder)
        if not base.is_dir():
            raise gr.Error(f"La carpeta no existe: {folder}")
        it = base.rglob("*") if recursive else base.glob("*")
        out += sorted(str(p) for p in it if p.is_file() and p.suffix.lower() in VIDEO_EXT)
    return out


# ---------------------------------------------------------------- tablas de revisión

def _rows(rows) -> list[list]:
    if hasattr(rows, "values") and hasattr(rows, "columns"):  # pandas.DataFrame
        return rows.values.tolist()
    return list(rows or [])


def _to_bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "sí", "si", "x", "yes")
    return bool(v)


def words_to_rows(words: list[Word]) -> list[list]:
    return [[w.text, round(w.start, 2), round(w.end, 2), bool(w.hl), int(w.grp)] for w in words]


def rows_to_words(rows) -> list[Word]:
    words = []
    for r in _rows(rows):
        if not r or len(r) < 3:
            continue
        text = str(r[0] if r[0] is not None else "").strip()
        if not text:
            continue  # vaciar la palabra = borrarla
        try:
            start, end = float(r[1]), float(r[2])
        except (TypeError, ValueError):
            continue
        hl = _to_bool(r[3]) if len(r) > 3 else False
        try:
            grp = int(float(r[4])) if len(r) > 4 and r[4] not in (None, "") else -1
        except (TypeError, ValueError):
            grp = -1
        words.append(Word(text, start, max(end, start + 0.05), 1.0, hl, grp))
    words.sort(key=lambda w: w.start)
    if any(w.grp < 0 for w in words):  # alguna palabra nueva sin frase → se reagrupa todo
        for w in words:
            w.grp = -1
    return words


def _entry_words(entry: dict) -> list[Word]:
    return words_from_json(entry.get("words") or [])


def _tables(entry: dict, sub_cfg: dict):
    words = _entry_words(entry)
    if words and any(w.grp < 0 for w in words):
        assign_groups(words, sub_cfg)
        entry["words"] = words_to_json(words)
    return phrase_rows(words, sub_cfg), words_to_rows(words)


# ---------------------------------------------------------------- callbacks: presets y vista previa

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
    keep = [gr.update()] * 7  # 4 de facecam + 3 de gameplay
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


# ---------------------------------------------------------------- callbacks: revisión

def _review_outputs(review: dict, path: str, sub_cfg: dict, msg: str = ""):
    entry = review.get(path) or {}
    if not entry:
        return review, gr.update(), gr.update(), gr.update(), gr.update(), msg
    phrases, words = _tables(entry, sub_cfg)
    return review, phrases, words, entry.get("hook", ""), entry.get("meta", ""), msg


def on_transcribe(files, folder, recursive, review: dict, preset_name: str, *values, progress=gr.Progress()):
    paths = gather_sources(files, folder, recursive)
    if not paths:
        raise gr.Error("Sube uno o varios vídeos (o indica una carpeta).")
    preset = values_to_preset(preset_name, list(values))
    review = dict(review or {})
    errors, notes = [], []
    n = len(paths)
    for i, src in enumerate(paths):
        name = Path(src).name

        def report(frac: float, msg: str, i=i, name=name):
            progress((i + frac) / n, desc=f"[{i + 1}/{n}] {name}: {msg}")

        try:
            words = transcribe_video(src, preset, report, notes=notes)
            res = None
            if llm_enabled(preset):
                try:
                    res = enrich_with_llm(words, preset, report)
                except Exception as e:
                    notes.append(f"{name}: LLM no disponible ({e})")
            assign_groups(words, preset["subtitles"])
            hook = res.hook if res and res.hook and preset["llm"].get("propose_hook", True) else preset["hook"].get("text", "")
            review[src] = {"words": words_to_json(words), "hook": hook, "meta": llm.format_metadata(res)}
        except Exception as e:
            logging.exception("Error transcribiendo %s", src)
            errors.append(f"{name}: {e}")
    choices = [(Path(p).name, p) for p in paths if p in review]
    if not choices:
        raise gr.Error("No se pudo transcribir ningún vídeo:\n" + "\n".join(errors))
    first = choices[0][1]
    msg = f"{len(choices)} vídeo(s) listos para revisar."
    if errors or notes:
        msg += "\n\n" + "\n".join(f"- {x}" for x in errors + notes)
    out = _review_outputs(review, first, preset["subtitles"], msg)
    return (out[0], gr.update(choices=choices, value=first), *out[1:])


def on_select_video(path, review: dict, preset_name: str, *values):
    sub_cfg = values_to_preset(preset_name, list(values))["subtitles"]
    return _review_outputs(review or {}, path, sub_cfg)[1:5]


def on_phrases_edit(rows, path, review: dict):
    """Se ha editado la tabla de subtítulos (texto o tiempos de una frase)."""
    entry = (review or {}).get(path)
    if not entry:
        return review, gr.update(), gr.update()
    words = apply_phrase_rows(_entry_words(entry), _rows(rows), {})
    entry["words"] = words_to_json(words)
    return review, phrase_rows(words, {}), words_to_rows(words)


def on_words_edit(rows, path, review: dict, preset_name: str, *values):
    """Se ha editado la tabla de palabras (texto, tiempos, resaltado o nº de frase)."""
    entry = (review or {}).get(path)
    if not entry:
        return review, gr.update()
    sub_cfg = values_to_preset(preset_name, list(values))["subtitles"]
    words = rows_to_words(rows)
    entry["words"] = words_to_json(words)
    phrases, _ = _tables(entry, sub_cfg)
    return review, phrases


def on_regroup(path, review: dict, preset_name: str, *values):
    entry = (review or {}).get(path)
    if not entry:
        raise gr.Error("Transcribe primero.")
    sub_cfg = values_to_preset(preset_name, list(values))["subtitles"]
    words = assign_groups(_entry_words(entry), sub_cfg)
    entry["words"] = words_to_json(words)
    return review, phrase_rows(words, sub_cfg), words_to_rows(words)


def on_llm_review(path, review: dict, preset_name: str, *values, progress=gr.Progress()):
    entry = (review or {}).get(path)
    if not entry:
        raise gr.Error("Transcribe primero.")
    preset = values_to_preset(preset_name, list(values))
    if preset["llm"].get("provider", "ninguno") == "ninguno":
        raise gr.Error("Elige un proveedor de LLM en la pestaña «IA (LLM)».")
    preset["toggles"]["llm_correction"] = True
    words = _entry_words(entry)
    try:
        res = enrich_with_llm(words, preset, lambda f, m: progress(f, desc=m))
    except Exception as e:
        raise gr.Error(f"LLM: {e}") from e
    entry["words"] = words_to_json(words)  # las correcciones son 1 palabra → 1 palabra: las frases se mantienen
    if res and res.hook and preset["llm"].get("propose_hook", True):
        entry["hook"] = res.hook
    entry["meta"] = llm.format_metadata(res)
    n = len(res.corrections) if res else 0
    return _review_outputs(review, path, preset["subtitles"], f"LLM aplicado: {n} corrección(es).")


def on_entry_field(field: str):
    def handler(text, path, review: dict):
        if path and path in (review or {}):
            review[path][field] = text
        return review
    return handler


# ---------------------------------------------------------------- callbacks: procesar

def _write_batch_csv(rows: list[dict]) -> Path:
    path = OUTPUT_DIR / f"lote_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    cols = ["archivo", "estado", "duracion_original", "duracion_final", "palabras", "cortes", "zooms", "sfx",
            "segundos_proceso", "salida", "error"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM: Excel abre bien las tildes
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    return path


def on_process(files, folder, recursive, skip_existing, review: dict, preset_name: str, *values,
               progress=gr.Progress()):
    paths = gather_sources(files, folder, recursive)
    if not paths:
        raise gr.Error("Sube uno o varios vídeos (o indica una carpeta).")
    preset = values_to_preset(preset_name, list(values))
    suffix = "short" if is_short(preset) else "largo"
    review = review or {}
    log: list[str] = []
    outputs: list[str] = []
    batch: list[dict] = []
    last_video = None
    n = len(paths)
    used: dict[str, int] = {}
    for i, src in enumerate(paths):
        stem = safe_stem(src)
        used[stem] = used.get(stem, 0) + 1
        if used[stem] > 1:  # mismo nombre en el lote: no sobrescribir
            stem = f"{stem}_{used[stem]}"
        name = Path(src).name
        if skip_existing and (OUTPUT_DIR / f"{stem}_{suffix}.mp4").exists():
            log.append(f"⏭ [{i + 1}/{n}] {name}: ya existe, se salta.")
            batch.append({"archivo": name, "estado": "saltado"})
            continue
        entry = review.get(src)
        log.append(f"▶ [{i + 1}/{n}] {name}" + ("  (transcripción revisada)" if entry else ""))
        yield "\n".join(log), outputs, last_video

        def report(frac: float, msg: str, i=i, name=name):
            progress((i + frac) / n, desc=f"[{i + 1}/{n}] {name}: {msg}")

        t0 = time.perf_counter()
        try:
            if entry:
                res = process_video(src, preset, progress=report, stem=stem, words=_entry_words(entry),
                                    reviewed=True, hook_text=entry.get("hook") or "",
                                    metadata_text=entry.get("meta") or "")
            else:
                res = process_video(src, preset, progress=report, stem=stem)
            outputs.extend(str(p) for p in res.files)
            last_video = str(res.video)
            log.append(res.summary)
            batch.append({"archivo": name, "estado": "ok", **res.stats,
                          "segundos_proceso": round(time.perf_counter() - t0, 1), "salida": res.video.name})
        except Exception as e:
            logging.exception("Error procesando %s", src)
            log.append(f"✖ Error en {name}: {e}\n{traceback.format_exc(limit=2)}")
            batch.append({"archivo": name, "estado": "error", "error": str(e)[:300],
                          "segundos_proceso": round(time.perf_counter() - t0, 1)})
        yield "\n".join(log), outputs, last_video
    if n > 1:
        csv_path = _write_batch_csv(batch)
        outputs.append(str(csv_path))
        count = {k: sum(1 for b in batch if b["estado"] == k) for k in ("ok", "error", "saltado")}
        log.append(f"Lote: {count['ok']} correctos, {count['error']} con error, {count['saltado']} saltados. "
                   f"Resumen: {csv_path.name}")
    log.append(f"Terminado. Archivos en: {OUTPUT_DIR}")
    yield "\n".join(log), outputs, last_video


# ---------------------------------------------------------------- UI

def build_ui() -> gr.Blocks:
    names = list_presets() or ["default"]
    first = "gta" if "gta" in names else names[0]
    v = dict(zip(KEYS, preset_to_values(load_preset(first))))
    comps: dict[str, gr.components.Component] = {}
    sfx_choices = [NO_SFX, *sfx.list_sfx()]

    def color(key, label):
        comps[key] = gr.ColorPicker(v[key], label=label)

    def check(key, label, **kw):
        comps[key] = gr.Checkbox(v[key], label=label, **kw)

    def slider(key, lo, hi, step, label, **kw):
        comps[key] = gr.Slider(lo, hi, v[key], step=step, label=label, **kw)

    with gr.Blocks(title="ClipForge") as demo:
        review_state = gr.State({})
        click_state = gr.State(None)
        gr.Markdown(f"# 🎮 ClipForge `v{__version__}`")

        with gr.Row():
            with gr.Column(scale=3):
                files = gr.File(label="Vídeos (arrastra uno o varios)", file_count="multiple",
                                file_types=["video"], type="filepath")
                with gr.Accordion("📁 Lote: procesar una carpeta", open=False):
                    with gr.Row():
                        folder = gr.Textbox(label="Carpeta (ruta en tu PC)", placeholder=r"D:\Grabaciones\GTA", scale=3)
                        recursive = gr.Checkbox(False, label="Incluir subcarpetas")
                        skip_existing = gr.Checkbox(True, label="Saltar los ya procesados")
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
                        check("toggles.subtitles", "Subtítulos")
                        check("toggles.hook_title", "Título gancho (Short)")
                        check("toggles.highlight_keywords", "Resaltar palabras clave")
                        check("toggles.export_srt", "Exportar .srt aparte")
                    with gr.Row():
                        check("toggles.cut_silences", "Cortar silencios")
                        check("toggles.auto_zoom", "Auto-zoom (palabras clave / gritos)")
                        check("toggles.sfx", "Efectos de sonido")
                        check("toggles.progress_bar", "Barra de progreso")
                    with gr.Row():
                        check("toggles.watermark", "Marca de agua / @handle")
                        check("toggles.isolate_voice", "Aislar voz (Demucs)")
                        check("toggles.llm_correction", "Corrección con LLM")
                    gr.Markdown("Los ajustes de cada efecto están en las pestañas **Efectos** e **IA (LLM)**. "
                                "Todo se guarda en el preset con 💾.")

                with gr.Tab("Layout"):
                    comps["layout.type"] = gr.Radio(
                        [("Facecam esquina", "facecam_esquina"), ("Split (facecam arriba)", "split"),
                         ("Blur (16:9 + fondo desenfocado)", "blur"), ("Sin facecam", "sin_facecam")],
                        value=v["layout.type"], label="Layout del Short")
                    gr.Markdown("**Facecam en el vídeo original** (% del ancho/alto · recuadro rojo). "
                                "También puedes marcarla con dos clics sobre el fotograma →")
                    with gr.Row():
                        slider("layout.facecam_rect.0", 0, 100, 0.5, "X %")
                        slider("layout.facecam_rect.1", 0, 100, 0.5, "Y %")
                        slider("layout.facecam_rect.2", 1, 100, 0.5, "Ancho %")
                        slider("layout.facecam_rect.3", 1, 100, 0.5, "Alto %")
                    autodetect_btn = gr.Button("🔎 Autodetectar facecam", size="sm")
                    gr.Markdown("**Facecam en el Short** (facecam esquina)")
                    with gr.Row():
                        comps["layout.facecam_position"] = gr.Dropdown(
                            [("Arriba derecha", "top-right"), ("Arriba izquierda", "top-left"), ("Arriba centro", "top-center")],
                            value=v["layout.facecam_position"], label="Posición")
                        slider("layout.facecam_width", 20, 100, 1, "Tamaño (% ancho)")
                        slider("layout.facecam_y", 0, 1200, 10, "Y (px)")
                    with gr.Row():
                        slider("layout.facecam_radius", 0, 80, 2, "Redondeo esquinas (px)")
                        check("layout.facecam_shadow", "Sombra suave")
                    gr.Markdown("**Gameplay** (recuadro azul)")
                    with gr.Row():
                        slider("layout.gameplay_offset", -1, 1, 0.05, "Desplazamiento X (-1 izq · 1 der)")
                        slider("layout.gameplay_offset_y", -1, 1, 0.05, "Desplazamiento Y (con zoom)")
                        slider("layout.gameplay_zoom", 1, 3, 0.05, "Zoom")
                    with gr.Row():
                        slider("layout.split_ratio", 15, 70, 1, "Split: alto de la facecam (%)")
                        slider("layout.blur_strength", 0, 60, 1, "Blur: desenfoque")
                        slider("layout.blur_fg_y", 300, 1620, 10, "Blur: centro Y del gameplay")

                with gr.Tab("Subtítulos"):
                    with gr.Row():
                        comps["subtitles.style"] = gr.Radio(
                            [("Frase", "frase"), ("Resaltado", "resaltado"), ("Karaoke", "karaoke")],
                            value=v["subtitles.style"], label="Estilo", info="Al cambiarlo se cargan sus valores típicos")
                        comps["subtitles.case"] = gr.Radio(
                            [("Original", "original"), ("minúsculas", "minusculas"), ("MAYÚSCULAS", "mayusculas")],
                            value=v["subtitles.case"], label="Mayúsculas")
                        check("subtitles.strip_punctuation", "Quitar puntos y comas")
                    with gr.Row():
                        comps["subtitles.font"] = gr.Textbox(v["subtitles.font"], label="Fuente (familia, en assets/fonts)")
                        slider("subtitles.font_size", 30, 160, 2, "Tamaño")
                        slider("subtitles.pos_y", 150, 1260, 10, "Posición Y (centro)", info="Short: nunca en los 660 px de abajo")
                    with gr.Row():
                        color("subtitles.primary_color", "Texto")
                        color("subtitles.outline_color", "Contorno")
                        color("subtitles.highlight_color", "Palabras clave")
                        color("subtitles.active_color", "Karaoke: palabra actual")
                    with gr.Row():
                        slider("subtitles.outline", 0, 12, 1, "Grosor contorno")
                        slider("subtitles.shadow", 0, 12, 1, "Sombra")
                        slider("subtitles.fade_ms", 0, 300, 10, "Fundido (ms)")
                    with gr.Row():
                        slider("subtitles.min_words", 1, 6, 1, "Mín. palabras")
                        slider("subtitles.max_words", 1, 10, 1, "Máx. palabras")
                        slider("subtitles.max_chars", 8, 60, 1, "Máx. caracteres")

                with gr.Tab("Título gancho"):
                    comps["hook.text"] = gr.Textbox(v["hook.text"], label="Texto por defecto (cada vídeo puede tener el suyo en «Revisar»)",
                                                    placeholder="UNA TRAGEDIA  ·  usa | para partir en dos líneas")
                    with gr.Row():
                        comps["hook.font"] = gr.Textbox(v["hook.font"], label="Fuente (se usa en cursiva)")
                        slider("hook.font_size", 50, 220, 2, "Tamaño")
                        check("hook.uppercase", "MAYÚSCULAS")
                    with gr.Row():
                        color("hook.fill", "Relleno")
                        color("hook.outline_color", "Contorno")
                        color("hook.outer_color", "Contorno exterior")
                    with gr.Row():
                        slider("hook.outline", 0, 30, 1, "Grosor contorno")
                        slider("hook.outer", 0, 30, 1, "Grosor exterior")
                        slider("hook.tilt", -15, 15, 1, "Giro (°)")
                    with gr.Row():
                        slider("hook.duration", 0, 10, 0.5, "Duración (s) · 0 = todo el clip")
                        slider("hook.pos_y", 0, 1260, 10, "Posición Y · 0 = auto (debajo de la facecam)")

                with gr.Tab("Efectos"):
                    gr.Markdown("**✂️ Cortar silencios** (quita las pausas; los subtítulos se reajustan solos)")
                    with gr.Row():
                        comps["silences.source"] = gr.Radio([("Audio del micro (umbral dB)", "audio"),
                                                             ("Huecos entre palabras", "palabras")],
                                                            value=v["silences.source"], label="Detectar con")
                        slider("silences.threshold_db", -60, -15, 1, "Umbral (dB)")
                        slider("silences.min_duration", 0.2, 3, 0.1, "Silencio mínimo (s)")
                        slider("silences.padding", 0, 0.5, 0.05, "Margen a cada lado (s)")
                    gr.Markdown("**🔍 Auto-zoom** (punch-in sobre el gameplay)")
                    with gr.Row():
                        check("zoom.on_keywords", "En palabras clave")
                        check("zoom.on_shouts", "En gritos")
                        slider("zoom.amount", 1.05, 1.5, 0.01, "Zoom (1.15 = 115 %)")
                        slider("zoom.shout_db", 3, 20, 1, "Grito: dB sobre lo normal")
                        slider("zoom.min_gap", 0.5, 10, 0.5, "Separación mínima (s)")
                    gr.Markdown("**🔊 Efectos de sonido** (pon tus .wav/.mp3 en `assets/sfx/`)")
                    with gr.Row():
                        comps["sfx.on_zoom"] = gr.Dropdown(sfx_choices, value=v["sfx.on_zoom"], label="Al hacer zoom")
                        comps["sfx.on_hook"] = gr.Dropdown(sfx_choices, value=v["sfx.on_hook"], label="Al salir el gancho")
                        comps["sfx.on_cut"] = gr.Dropdown(sfx_choices, value=v["sfx.on_cut"], label="En cada corte")
                        slider("sfx.volume", 0, 1.5, 0.05, "Volumen")
                    gr.Markdown("**▬ Barra de progreso**")
                    with gr.Row():
                        color("progress_bar.color", "Color")
                        slider("progress_bar.height", 2, 40, 1, "Grosor (px)")
                        comps["progress_bar.position"] = gr.Radio([("Arriba", "arriba"), ("Abajo", "abajo")],
                                                                  value=v["progress_bar.position"], label="Posición")
                    gr.Markdown("**©️ Marca de agua**")
                    with gr.Row():
                        comps["handle"] = gr.Textbox(v["handle"], label="@handle")
                        comps["watermark.position"] = gr.Dropdown(list(WATERMARK_POSITIONS), value=v["watermark.position"],
                                                                  label="Posición")
                        slider("watermark.opacity", 10, 100, 5, "Opacidad %")
                        slider("watermark.font_size", 20, 100, 2, "Tamaño")
                        color("watermark.color", "Color")

                with gr.Tab("IA (LLM)"):
                    gr.Markdown("Corrige la transcripción, elige palabras clave, propone el gancho y escribe títulos, "
                                "descripción y hashtags. Activa también el interruptor **Corrección con LLM**. "
                                "Claves en `.env` (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`); Ollama en `OLLAMA_HOST`.")
                    with gr.Row():
                        comps["llm.provider"] = gr.Radio([("Ninguno", "ninguno"), ("Ollama (local, gratis)", "ollama"),
                                                          ("Claude API", "claude"), ("OpenAI API", "openai")],
                                                         value=v["llm.provider"], label="Proveedor")
                    with gr.Row():
                        comps["llm.ollama_model"] = gr.Textbox(v["llm.ollama_model"], label="Modelo Ollama")
                        comps["llm.claude_model"] = gr.Textbox(v["llm.claude_model"], label="Modelo Claude")
                        comps["llm.openai_model"] = gr.Textbox(v["llm.openai_model"], label="Modelo OpenAI")
                    with gr.Row():
                        check("llm.correct_text", "Corregir texto")
                        check("llm.pick_keywords", "Elegir palabras clave")
                        check("llm.propose_hook", "Proponer gancho")
                        check("llm.metadata", "Títulos, descripción y hashtags")

                with gr.Tab("Motor de render"):
                    ok, why = remotion_render.status()
                    gr.Markdown(
                        "**FFmpeg** es rápido y vale para todo. **Remotion** (solo Shorts) dibuja los subtítulos, "
                        "el gancho y el zoom con animaciones más suaves (rebote al entrar, deslizamiento…), pero "
                        "tarda bastante más. Los dos usan los mismos ajustes del resto de pestañas.\n\n"
                        + ("✅ " if ok else "⚠️ ") + why
                    )
                    with gr.Row():
                        comps["render.engine"] = gr.Radio([("FFmpeg (rápido)", "ffmpeg"),
                                                           ("Remotion (animaciones avanzadas)", "remotion")],
                                                          value=v["render.engine"], label="Motor para los Shorts")
                        comps["subtitles.animation"] = gr.Radio([("Pop", "pop"), ("Deslizar", "slide"),
                                                                 ("Fundido", "fade"), ("Ninguna", "none")],
                                                                value=v["subtitles.animation"],
                                                                label="Entrada de cada frase (Remotion)")

                with gr.Tab("Transcripción y audio"):
                    with gr.Row():
                        comps["transcription.model"] = gr.Dropdown(["large-v3-turbo", "large-v3", "medium", "small"],
                                                                   value=v["transcription.model"], label="Modelo Whisper")
                        comps["transcription.device"] = gr.Dropdown([("Auto", "auto"), ("GPU (CUDA)", "cuda"), ("CPU", "cpu")],
                                                                    value=v["transcription.device"], label="Dispositivo")
                        check("transcription.reuse_cache", "Reusar transcripción si ya existe")
                    comps["transcription.glossary"] = gr.Textbox(v["transcription.glossary"], lines=2,
                                                                 label="Glosario del juego (nombres propios, jerga; también se resaltan)")
                    with gr.Row():
                        comps["audio.transcribe_track"] = gr.Number(v["audio.transcribe_track"], precision=0,
                                                                    label="Pista del micro (transcribir, silencios, gritos)")
                        comps["audio.output_track"] = gr.Dropdown(["0", "1", "2", "3", "mix"], value=v["audio.output_track"],
                                                                  label="Pista de audio del vídeo final", allow_custom_value=True)
                    with gr.Row():
                        comps["render.encoder"] = gr.Dropdown([("Auto (NVENC si hay NVIDIA)", "auto"), ("NVENC", "nvenc"), ("CPU x264", "x264")],
                                                              value=v["render.encoder"], label="Encoder")
                        comps["render.long_resolution"] = gr.Dropdown([("Original", "original"), ("1080p", "1080p")],
                                                                      value=v["render.long_resolution"], label="Resolución Modo Largo")

                with gr.Tab("Revisar y corregir"):
                    gr.Markdown("1) **Transcribir** · 2) corrige el **texto de los subtítulos** tal y como saldrán en "
                                "pantalla (o palabra a palabra) · 3) revisa gancho y textos · 4) **Procesar**. "
                                "Los vídeos sin revisar se transcriben al procesar.")
                    with gr.Row():
                        transcribe_btn = gr.Button("📝 Transcribir para revisar", variant="secondary")
                        video_dd = gr.Dropdown([], label="Vídeo", interactive=True)
                    review_msg = gr.Markdown()
                    with gr.Row():
                        hook_box = gr.Textbox(label="Título gancho de este vídeo", placeholder="UNA TRAGEDIA", scale=2)
                        regroup_btn = gr.Button("↻ Reagrupar con el estilo actual", size="sm", scale=1)
                        llm_btn = gr.Button("🤖 Pasar por el LLM", size="sm", scale=1)
                    with gr.Tab("Subtítulos (frases)"):
                        gr.Markdown("Cada fila es un subtítulo tal y como aparece. Cambia el texto para corregir errores "
                                    "(si mantienes el nº de palabras, el karaoke conserva los tiempos exactos), ajusta "
                                    "inicio/fin o **vacía el texto para borrar** el subtítulo.")
                        phrase_table = gr.Dataframe(headers=PHRASE_HEADERS, datatype=["number", "number", "str"],
                                                    type="array", interactive=True, column_count=(3, "fixed"),
                                                    label="Subtítulos (segundos)", max_height=520, wrap=True)
                    with gr.Tab("Palabras"):
                        gr.Markdown("Ajuste fino: texto y tiempos de cada palabra, qué se **resalta** y a qué **frase** "
                                    "(nº de subtítulo) pertenece.")
                        word_table = gr.Dataframe(headers=WORD_HEADERS, datatype=["str", "number", "number", "bool", "number"],
                                                  type="array", interactive=True, column_count=(5, "fixed"),
                                                  label="Palabras (segundos)", max_height=520)
                    meta_box = gr.Textbox(label="Títulos, descripción y hashtags (LLM; editable, va a _meta.txt)", lines=6)

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
        review_out = [review_state, phrase_table, word_table, hook_box, meta_box, review_msg]

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

        transcribe_btn.click(on_transcribe, [files, folder, recursive, review_state, preset_dd, *inputs],
                             [review_state, video_dd, *review_out[1:]])
        video_dd.change(on_select_video, [video_dd, review_state, preset_dd, *inputs],
                        [phrase_table, word_table, hook_box, meta_box])
        phrase_table.input(on_phrases_edit, [phrase_table, video_dd, review_state],
                           [review_state, phrase_table, word_table])
        word_table.input(on_words_edit, [word_table, video_dd, review_state, preset_dd, *inputs],
                         [review_state, phrase_table])
        regroup_btn.click(on_regroup, [video_dd, review_state, preset_dd, *inputs],
                          [review_state, phrase_table, word_table])
        llm_btn.click(on_llm_review, [video_dd, review_state, preset_dd, *inputs], review_out)
        hook_box.change(on_entry_field("hook"), [hook_box, video_dd, review_state], review_state)
        meta_box.change(on_entry_field("meta"), [meta_box, video_dd, review_state], review_state)
        process_btn.click(on_process, [files, folder, recursive, skip_existing, review_state, preset_dd, *inputs],
                          [log_box, files_out, video_out])
    return demo


if __name__ == "__main__":
    port = int(os.environ.get("CLIPFORGE_PORT", "7860"))
    build_ui().queue().launch(
        server_name="127.0.0.1",
        server_port=port,
        inbrowser=os.environ.get("CLIPFORGE_NO_BROWSER") != "1",
        allowed_paths=[str(OUTPUT_DIR)],
    )
