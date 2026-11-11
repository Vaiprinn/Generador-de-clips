"""ClipForge — interfaz Gradio (localhost).

Ejecuta:  python app.py   (o run.bat en Windows)
"""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import tempfile
import time
import traceback
from pathlib import Path

import gradio as gr
from PIL import Image

from pipeline import __version__, highlights, llm, media, remotion_render, sfx
from pipeline.job import (_hook_cfg, enrich_with_llm, is_short, llm_enabled, process_video, render_preview_frame,
                          safe_stem, transcribe_video)
from pipeline.layout import OUT_H, OUT_W, compute_short_layout, gameplay_dst_aspect
from pipeline.paths import FONTS_DIR, OUTPUT_DIR, WORK_DIR, ensure_dirs
from pipeline.phrases import apply_phrase_rows, assign_groups, phrase_rows
from pipeline.presets import get_path, list_presets, load_preset, save_preset, set_path
from pipeline.subtitles import (SAFE_LEFT_PX, SAFE_RIGHT_PX, STYLE_DEFAULTS, WATERMARK_POSITIONS, build_phrases,
                                safe_position)
from pipeline.transcribe import Word, words_from_json, words_to_json
from ui_editor import editor
from ui_short import short_preview
from ui_theme import CSS, JS, build_theme, header_html, step_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ensure_dirs()

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".ts"}

def rect_list(v) -> list[float]:
    """Recuadro [x, y, ancho, alto] (fracciones) desde su texto JSON; [] si no hay o no es válido."""
    try:
        r = json.loads(v) if isinstance(v, str) else list(v or [])
        return [round(min(max(float(x), 0.0), 1.0), 4) for x in r] if len(r) == 4 else []
    except (TypeError, ValueError):
        return []


# (ruta en el preset, conversión al guardar). El orden define el orden de los inputs.
FIELDS: list[tuple[str, type]] = [
    ("mode", str),
    ("layout.type", str),
    ("layout.facecam_rect.0", float),
    ("layout.facecam_rect.1", float),
    ("layout.facecam_rect.2", float),
    ("layout.facecam_rect.3", float),
    ("layout.facecam_position", str),
    ("layout.facecam_x", int),
    ("layout.facecam_width", float),
    ("layout.facecam_y", int),
    ("layout.facecam_radius", int),
    ("layout.facecam_shadow", bool),
    ("layout.gameplay_offset", float),
    ("layout.gameplay_offset_y", float),
    ("layout.gameplay_zoom", float),
    ("layout.gameplay_rect", rect_list),
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
    ("toggles.speakers", bool),
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
    *[(f"subtitles.speaker_colors.{i}", str) for i in range(4)],
    *[(f"subtitles.speaker_show.{i}", bool) for i in range(4)],
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
    ("transcription.dialect", str),
    ("transcription.speakers", int),
    ("transcription.speaker_threshold", float),
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
WORD_HEADERS = ["palabra", "inicio", "fin", "resaltar", "frase", "voz"]
PHRASE_HEADERS = ["inicio", "fin", "texto del subtítulo"]
NO_SFX = "(ninguno)"


def _list_key(key: str) -> tuple[str, int] | None:
    """'layout.facecam_rect.2' → ('layout.facecam_rect', 2): un elemento de una lista del preset."""
    base, _, idx = key.rpartition(".")
    return (base, int(idx)) if idx.isdigit() else None


def _get(preset: dict, key: str):
    if (lk := _list_key(key)):
        return get_path(preset, lk[0])[lk[1]]
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
        if key == "layout.gameplay_rect":
            v = json.dumps(rect_list(v))
        vals.append(v)
    return vals


def values_to_preset(base_name: str, values: list) -> dict:
    preset = load_preset(base_name or "default")
    for (key, conv), v in zip(FIELDS, values):
        if v is None:
            continue
        if key in PCT_FIELDS:
            v = float(v) / 100
        if key.startswith("sfx.on_") and v == NO_SFX:
            v = ""
        v = conv(v)
        if (lk := _list_key(key)):
            items = list(get_path(preset, lk[0]))
            items[lk[1]] = v
            set_path(preset, lk[0], items)
        else:
            set_path(preset, key, v)
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
    return [[w.text, round(w.start, 2), round(w.end, 2), bool(w.hl), int(w.grp), w.spk + 1 if w.spk >= 0 else None]
            for w in words]


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
        try:  # voz: 1, 2, 3… en la tabla (vacío = sin detectar)
            spk = int(float(r[5])) - 1 if len(r) > 5 and r[5] not in (None, "") else -1
        except (TypeError, ValueError):
            spk = -1
        words.append(Word(text, start, max(end, start + 0.05), 1.0, hl, grp, spk))
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
    return gr.update(choices=preset_choices(), value=path.stem), f"Preset guardado: `{path.name}`"


PRESET_LABELS = {"gta": "🚗 GTA", "eafc27": "⚽ EA FC 27", "thefinals": "🔫 The Finals", "minecraft": "⛏️ Minecraft",
                 "largo_youtube": "📺 YouTube largo"}
DIALECTS = [("🌎 Latinoamérica (general)", "latam"), ("🇲🇽 México", "mx"), ("🇨🇴 Colombia", "co"),
            ("🇦🇷 Argentina / Uruguay", "ar"), ("🇨🇱 Chile", "cl"), ("🇵🇪 Perú", "pe"), ("🇻🇪 Venezuela", "ve"),
            ("🇪🇸 España", "es")]


def preset_choices() -> list[tuple[str, str]]:
    return [(PRESET_LABELS.get(n, f"⭐ {n}"), n) for n in (list_presets() or ["default"])]


def font_choices(current: str = "") -> list[str]:
    names = sorted({e["family"] for e in _font_entries()} | {current} - {""})
    return names


def on_style_input(style: str):
    d = STYLE_DEFAULTS.get(style, {})
    return [gr.update(value=d[k.split(".", 1)[1]]) if k.split(".", 1)[1] in d else gr.update() for k in STYLE_KEYS]


FACECAM_LAYOUTS = ("facecam_esquina", "split")
KEY_INDEX = {k: i for i, k in enumerate(KEYS)}


def _frame_data_url(path: Path, max_w: int = 960) -> str:
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_w, max_w))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _editor_value(frame: Path, info, preset: dict) -> str:
    """Valor del editor de encuadre: fotograma + recuadros de facecam y gameplay en fracciones."""
    W, H = info.width, info.height
    data = {"src": _frame_data_url(frame), "W": W, "H": H, "fc": None, "gp": None, "aspect": None}
    if is_short(preset):
        cfg = preset["layout"]
        lay = compute_short_layout(W, H, cfg)
        g = lay.gameplay
        data["gp"] = [round(g.x / W, 4), round(g.y / H, 4), round(g.w / W, 4), round(g.h / H, 4)]
        data["aspect"] = gameplay_dst_aspect(W, H, {**cfg, "type": lay.type})
        if lay.type in FACECAM_LAYOUTS:
            data["fc"] = [round(float(v), 4) for v in cfg["facecam_rect"]]
    return json.dumps(data)


def _render_result(src: str, preset: dict, t: float, review: dict | None, tmp: Path) -> Image.Image:
    hook_text = (review or {}).get(src, {}).get("hook") or None
    out = render_preview_frame(src, preset, t, tmp / "out.png", hook_text=hook_text)
    result = Image.open(out).convert("RGB")
    result.thumbnail((540, 960) if is_short(preset) else (960, 540))
    return result


SAMPLE_PHRASES = ["qué locura de partida chicos", "no me lo puedo creer", "vamos con todo", "esto se puso intenso"]
# Ajustes que la vista previa en vivo cambia al arrastrar (clave del preset → valor ya en unidades de la interfaz).
SHORT_EDIT_KEYS = ["layout.facecam_position", "layout.facecam_x", "layout.facecam_y", "layout.facecam_width",
                   "layout.split_ratio", "layout.blur_fg_y", "subtitles.pos_y", "subtitles.font_size",
                   "hook.pos_y", "hook.font_size"]


def _file_url(path: str | Path) -> str:
    return "/gradio_api/file=" + str(Path(path).resolve()).replace("\\", "/")


def _font_entries() -> list[dict]:
    """Fuentes de assets/fonts para el <canvas>, registradas con todos sus nombres posibles."""
    from PIL import ImageFont

    out = []
    for p in sorted(FONTS_DIR.glob("*.[ot]tf")):
        names = {p.stem.replace("-", " ")}
        try:
            fam, sty = ImageFont.truetype(str(p), 20).getname()
            names |= {fam, f"{fam} {sty}".strip()}
        except OSError:
            pass
        out += [{"family": n, "url": _file_url(p)} for n in sorted(names) if n]
    return out


def _rect(r) -> list[int] | None:
    return [r.x, r.y, r.w, r.h] if r else None


def short_preview_value(src: str, info, preset: dict, review: dict | None, trim: list | None) -> str:
    """Datos para la vista previa en vivo (ui_short): geometría del layout, subtítulo, gancho y clip."""
    short = is_short(preset)
    toggles, sub = preset["toggles"], preset["subtitles"]
    entry = (review or {}).get(src, {})
    phrases = SAMPLE_PHRASES
    if entry.get("words"):
        groups = build_phrases(words_from_json(entry["words"]), sub)
        phrases = [" ".join(w.text for w in p.words) for p in groups][:12] or SAMPLE_PHRASES
    out_w, out_h = (OUT_W, OUT_H) if short else (1920, 1080)
    sx, sy = safe_position(sub, out_w, out_h, short)
    k = out_h / (1920 if short else 1080)
    data = {
        "video": _file_url(src), "W": info.width, "H": info.height, "dur": round(info.duration, 3),
        "fps": float(info.fps), "short": short,
        "trim": trim or [0, round(info.duration, 3)],
        "fonts": _font_entries(),
        "sub": {
            "show": bool(toggles.get("subtitles", True)), "phrases": phrases,
            "font": sub.get("font", "Nunito Black"), "size": round(float(sub.get("font_size", 72)) * k),
            "color": sub.get("primary_color", "#FFFFFF"), "outlineColor": sub.get("outline_color", "#000000"),
            "outline": float(sub.get("outline", 4)) * k, "shadow": float(sub.get("shadow", 3)) * k,
            "case": sub.get("case", "original"), "x": sx, "y": sy,
            "maxW": out_w - (SAFE_LEFT_PX + SAFE_RIGHT_PX if short else 120),
        },
    }
    if short:
        lay = compute_short_layout(info.width, info.height, preset["layout"])
        hook = _hook_cfg(preset, entry.get("hook") or preset["hook"].get("text") or "una tragedia", lay.hook_y)
        data.update({
            "type": lay.type, "gp": _rect(lay.gameplay), "gpDst": _rect(lay.gameplay_dst), "fit": lay.fit,
            "fcSrc": _rect(lay.facecam_src), "fcDst": _rect(lay.facecam_dst), "bg": _rect(lay.background),
            "radius": int(preset["layout"].get("facecam_radius", 28)),
            "shadow": bool(preset["layout"].get("facecam_shadow", True)),
            "blur": int(preset["layout"].get("blur_strength", 20)),
            "pb": ({"color": preset["progress_bar"]["color"], "h": int(preset["progress_bar"]["height"]),
                    "pos": preset["progress_bar"]["position"]} if toggles.get("progress_bar") else None),
            "hook": ({"show": True, "text": format_hook_text(hook), "font": hook.get("font", "Montserrat Black"),
                      "size": int(hook.get("font_size", 120)), "fill": hook.get("fill", "#FFFFFF"),
                      "outlineColor": hook.get("outline_color", "#E3262E"), "outline": float(hook.get("outline", 12)),
                      "outerColor": hook.get("outer_color", "#FFFFFF"), "outer": float(hook.get("outer", 10)),
                      "y": int(hook["pos_y"]), "tilt": float(hook.get("tilt", 0)),
                      "dur": float(hook.get("duration", 2.5))} if hook else None),
        })
    return json.dumps(data)


def format_hook_text(hook: dict) -> str:
    text = (hook.get("text") or "").strip()
    return text.upper() if hook.get("uppercase", True) else text


def _info_msg(src: str, info) -> str:
    audio = ", ".join(
        f"{a['index']}: {a['title'] or a['codec']} ({a['channels']} ch)" for a in info.audio_tracks
    ) or "sin audio"
    return (f"`{Path(src).name}` · {info.width}x{info.height} · {float(info.fps):.0f} fps · "
            f"{info.duration:.1f} s · audio: {audio}")


def _short_value(files, preset_name, values, review, trims) -> str:
    paths = _paths(files)
    if not paths:
        return "{}"
    src = paths[0]
    try:
        return short_preview_value(src, media.probe(src), values_to_preset(preset_name, list(values)),
                                   review, (trims or {}).get(src))
    except Exception as e:
        logging.warning("Vista previa en vivo: %s", e)
        return gr.update()


def _preview(files, preset_name, t, values, review=None, trims=None, quiet=False):
    """(editor de encuadre, vista previa en vivo, mensaje). Con `quiet` no da error si aún no hay vídeo."""
    paths = _paths(files)
    if not paths:
        if quiet:
            return "{}", "{}", ""
        raise gr.Error("Sube al menos un vídeo para ver la vista previa.")
    src = paths[0]
    preset = values_to_preset(preset_name, list(values))
    tmp = Path(tempfile.mkdtemp(prefix="pv_", dir=WORK_DIR))
    try:
        info = media.probe(src)
        t = max(0.0, min(float(t), max(info.duration - 0.2, 0.0)))
        frame = media.extract_frame(src, tmp / "src.png", t)
        live = short_preview_value(src, info, preset, review, (trims or {}).get(src))
        return _editor_value(frame, info, preset), live, _info_msg(src, info)
    except gr.Error:
        raise
    except Exception as e:
        raise gr.Error(str(e)) from e


def on_preview(files, preset_name: str, t: float, review: dict, trims: dict, *values):
    return _preview(files, preset_name, t, values, review, trims)


def on_auto_preview(files, preset_name: str, t: float, review: dict, trims: dict, *values):
    return _preview(files, preset_name, t, values, review, trims, quiet=True)


def on_live_refresh(files, preset_name: str, t: float, review: dict, trims: dict, *values):
    """Cualquier ajuste cambiado: se redibuja la vista previa en vivo (instantáneo, sin FFmpeg)."""
    return _short_value(files, preset_name, values, review, trims)


def on_exact_frame(files, preset_name: str, t: float, review: dict, trims: dict, *values):
    paths = _paths(files)
    if not paths:
        raise gr.Error("Sube un vídeo primero.")
    tmp = Path(tempfile.mkdtemp(prefix="pv_", dir=WORK_DIR))
    try:
        return _render_result(paths[0], values_to_preset(preset_name, list(values)), float(t), review, tmp)
    except Exception as e:
        raise gr.Error(str(e)) from e


def on_editor_input(value: str, files, preset_name: str, t: float, review: dict, trims: dict, *values):
    """El usuario ha movido un recuadro del original: se guardan y se redibuja la vista previa en vivo."""
    try:
        data = json.loads(value or "{}")
    except ValueError:
        data = {}
    vals = list(values)
    upd = [gr.update()] * 5  # 4 de facecam (%) + recuadro del gameplay
    fc, gp = rect_list(data.get("fc")), rect_list(data.get("gp"))
    if fc:
        for i in range(4):
            v = round(fc[i] * 100, 1)
            vals[KEY_INDEX[f"layout.facecam_rect.{i}"]] = v
            upd[i] = gr.update(value=v)
    if gp:
        vals[KEY_INDEX["layout.gameplay_rect"]] = json.dumps(gp)
        upd[4] = gr.update(value=json.dumps(gp))
    return [*upd, _short_value(files, preset_name, vals, review, trims), "Encuadre actualizado."]


def on_short_input(value: str, files, preset_name: str, t: float, review: dict, trims: dict, *values):
    """Algo se ha arrastrado en la vista previa en vivo (o se ha cambiado el trozo del clip)."""
    try:
        data = json.loads(value or "{}")
    except ValueError:
        data = {}
    vals = list(values)
    upd = [gr.update()] * len(SHORT_EDIT_KEYS)
    for key, v in (data.get("edit") or {}).items():
        if key in SHORT_EDIT_KEYS:
            vals[KEY_INDEX[key]] = v
            upd[SHORT_EDIT_KEYS.index(key)] = gr.update(value=v)
    trims = dict(trims or {})
    paths = _paths(files)
    if paths and isinstance(data.get("trim"), list) and len(data["trim"]) == 2:
        trims[paths[0]] = [round(float(x), 2) for x in data["trim"]]
    t_new = round(float(data.get("t", t) or 0), 2)
    return [*upd, trims, gr.update(value=t_new), _short_value(files, preset_name, vals, review, trims)]


def on_reset_framing():
    return gr.update(value="[]")


def on_autodetect(files, quiet: bool = False):
    paths = _paths(files)
    if not paths:
        if quiet:
            return [gr.update()] * 4 + [""]
        raise gr.Error("Sube un vídeo primero.")
    from pipeline.facedetect import DetectorUnavailable, detect_facecam

    try:
        rect = detect_facecam(paths[0])
    except DetectorUnavailable as e:
        if quiet:
            return [gr.update()] * 4 + [f"⚠️ {e}"]
        raise gr.Error(str(e)) from e
    if rect is None:
        msg = ("No veo ninguna webcam en este vídeo. Si no sales en cámara, elige el layout **Sin facecam** o "
               "**Blur**; si sí sales, coloca el recuadro rojo a mano.")
        if quiet:
            return [gr.update()] * 4 + [msg]
        raise gr.Error(msg)
    return [gr.update(value=round(v * 100, 1)) for v in rect] + ["✓ Webcam detectada. Revisa el recuadro rojo."]


def on_upload_detect(files, layout_type: str):
    """Al subir un vídeo se busca la webcam sola si el layout la usa."""
    if layout_type not in FACECAM_LAYOUTS:
        return [gr.update()] * 4 + [""]
    return on_autodetect(files, quiet=True)


# ---------------------------------------------------------------- callbacks: tus clips

def list_clips() -> list[tuple[str, str]]:
    clips = sorted(OUTPUT_DIR.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [(f"{p.relative_to(OUTPUT_DIR).as_posix()}  ·  {time.strftime('%d/%m %H:%M', time.localtime(p.stat().st_mtime))}",
             str(p)) for p in clips]


def on_refresh_clips(select_newest: bool = True):
    clips = list_clips()
    value = clips[0][1] if clips and select_newest else None
    return gr.update(choices=clips, value=value), value


def on_select_clip(path):
    return path or None


def on_open_output():
    if hasattr(os, "startfile"):
        os.startfile(OUTPUT_DIR)  # noqa: S606 (abre la carpeta en el Explorador de Windows)
        return "Carpeta abierta en el Explorador."
    return f"Tus clips están en: `{OUTPUT_DIR}`"


# ---------------------------------------------------------------- callbacks: buscar momentos (VOD)

MOMENT_COLS = ["✔", "#", "Inicio", "Fin", "Seg.", "Punt.", "Gancho", "Título", "Por qué"]

# ---- «¿Qué quieres hacer?»: cada acción decide el formato y qué tarjetas se ven
ACTIONS = [
    ("✂️ Convertir en Short · un clip ya cortado → vertical 9:16 con subtítulos", "short"),
    ("🔎 Sacar los mejores momentos · de un directo largo salen varios Shorts", "momentos"),
    ("🖥️ Vídeo para YouTube · 16:9 con subtítulos y sin silencios", "largo"),
]
ACTION_FROM_MODE = {"short": "short", "largo": "largo"}
ACTION_HINTS = {
    "short": "Sube uno o varios clips, elige el juego y los extras, coloca el encuadre y pulsa **Generar**.",
    "momentos": "Sube el directo (o elígelo del PC abajo) y pulsa **Buscar momentos**. Con 7 h de directo "
                "tarda unos 15 min en tu PC; mientras, puedes ajustar el encuadre de los Shorts.",
    "largo": "Sube el vídeo, elige los extras (subtítulos, cortar silencios…) y pulsa **Generar**.",
}
LONG_VIDEO_S = 10 * 60  # a partir de aquí se sugiere «Sacar los mejores momentos»


def _fmt_dur(s: float) -> str:
    h, m = divmod(int(s) // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {int(s) % 60:02d} s"


def on_action(act: str):
    """Muestra solo lo que hace falta para la acción elegida."""
    short = act in ("short", "momentos")
    label = "🚀 Generar Shorts" if act == "short" else "🚀 Generar vídeo para YouTube"
    return (gr.update(value="short" if short else "largo"),
            gr.update(visible=act == "momentos"),
            gr.update(visible=short),
            gr.update(visible=act != "momentos"),
            gr.update(value=label),
            ACTION_HINTS.get(act, ""))


def on_action_keep_note(act: str):
    """Como on_action pero sin tocar la nota (p. ej. tras sugerir la acción al subir un vídeo)."""
    return on_action(act)[:-1]


def on_mode_sync(mode: str, act: str):
    """Si un preset cambia el formato (p. ej. «YouTube largo»), la acción lo sigue."""
    if mode == "largo" and act != "largo":
        return "largo"
    if mode == "short" and act == "largo":
        return "short"
    return gr.update()


def on_suggest_action(files, act: str):
    paths = _paths(files)
    if len(paths) != 1:
        return gr.update(), ACTION_HINTS.get(act, "")
    try:
        dur = media.probe(paths[0]).duration
    except Exception:
        return gr.update(), ACTION_HINTS.get(act, "")
    if dur >= LONG_VIDEO_S and act != "momentos":
        return "momentos", (f"📼 Tu vídeo dura **{_fmt_dur(dur)}**: parece un directo, así que he elegido "
                            f"**Sacar los mejores momentos**. Cámbialo si quieres otra cosa.")
    return gr.update(), f"📼 Tu vídeo dura **{_fmt_dur(dur)}**. " + ACTION_HINTS.get(act, "")


def on_pick_vod(current: str):
    """Abre el selector de archivos de Windows (la app corre en este PC): el directo no se copia."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        start = Path(current).parent if current and Path(current).parent.is_dir() else Path.home() / "Videos"
        path = filedialog.askopenfilename(
            parent=root, title="Elige el directo", initialdir=str(start),
            filetypes=[("Vídeos", " ".join(f"*{e}" for e in sorted(VIDEO_EXT))), ("Todos los archivos", "*.*")])
        root.destroy()
    except Exception as e:
        raise gr.Error(f"No se pudo abrir el selector de archivos: {e}. Escribe la ruta a mano.") from e
    return path or current


def _moment_rows(clips: list[highlights.Clip]) -> list[list]:
    return [[c.keep, i + 1, highlights.fmt_time(c.start), highlights.fmt_time(c.end), round(c.duration, 1),
             c.score, c.hook, c.title, c.why] for i, c in enumerate(clips)]


def _vod_path(vod_path: str, files) -> str:
    path = (vod_path or "").strip().strip('"')
    if not path:
        up = _paths(files)
        path = up[0] if up else ""
    if not path:
        raise gr.Error("Escribe la ruta del directo (o sube el vídeo arriba).")
    if not Path(path).is_file():
        raise gr.Error(f"No existe el archivo: {path}")
    return path


def on_find_moments(vod_path, files, chat_file, n_clips, transcribe_mode, scenes, provider, preset_name, *values,
                    progress=gr.Progress()):
    src = _vod_path(vod_path, files)
    preset = values_to_preset(preset_name, list(values))
    preset["llm"]["provider"] = provider
    preset["highlights"] = {**(preset.get("highlights") or {}), "max_clips": int(n_clips),
                            "transcribe": transcribe_mode, "scenes": bool(scenes)}
    chat = _paths([chat_file])[0] if chat_file else None
    t0 = time.perf_counter()
    try:
        res = highlights.find_highlights(src, preset, chat, lambda f, m: progress(f, desc=m))
    except Exception as e:
        logging.exception("Buscar momentos")
        raise gr.Error(f"No se pudieron buscar momentos: {e}") from e
    t = ", ".join(f"{k} {v:.0f} s" for k, v in res.timings.items())
    log = (f"✅ {len(res.clips)} clips propuestos de {res.duration / 60:.0f} min en {time.perf_counter() - t0:.0f} s ({t}).\n"
           f"Carpeta: {res.out_dir}\n" + "".join(f"Aviso: {n}\n" for n in res.notes)
           + "\nDesmarca ✔ los que no quieras y corrige inicio, fin, gancho o título en la tabla.")
    return {"src": src, "dir": str(res.out_dir)}, _moment_rows(res.clips), log


def on_render_moments(state, rows, preset_name, *values, progress=gr.Progress()):
    if not state or not state.get("dir"):
        raise gr.Error("Primero pulsa «Buscar momentos».")
    out_dir = Path(state["dir"])
    data = json.loads((out_dir / "candidatos.json").read_text(encoding="utf-8"))
    words = words_from_json(json.loads((out_dir / "palabras.json").read_text(encoding="utf-8")))
    clips: list[highlights.Clip] = []
    for r in _rows(rows):
        if len(r) < len(MOMENT_COLS) or not r[0] or str(r[0]).lower() in ("false", "0"):
            continue
        try:
            start, end = highlights.parse_time(r[2]), highlights.parse_time(r[3])
        except ValueError:
            raise gr.Error(f"Tiempo no válido en el clip {r[1]}: «{r[2]}» / «{r[3]}» (usa hh:mm:ss.s)")
        if end - start < 3:
            raise gr.Error(f"El clip {r[1]} dura menos de 3 s.")
        orig = data["clips"][int(r[1]) - 1] if 0 < int(r[1] or 0) <= len(data["clips"]) else {}
        clips.append(highlights.Clip(start, end, float(r[5] or 0), str(r[8] or ""), str(r[6] or ""), str(r[7] or ""),
                                     list(orig.get("hashtags") or [])))
    if not clips:
        raise gr.Error("No hay ningún clip marcado con ✔.")
    preset = values_to_preset(preset_name, list(values))
    results = highlights.render_clips(state["src"], clips, words, preset, out_dir, lambda f, m: progress(f, desc=m))
    log, files_out, last = [], [], None
    for c, res in results:
        name = c.title or c.hook or highlights.fmt_time(c.start)
        if isinstance(res, Exception):
            log.append(f"✖ {name}: {res}")
        else:
            log.append(f"✔ {res.video.name}  ({c.duration:.0f} s)")
            files_out.extend(str(p) for p in res.files)
            last = str(res.video)
    log.append(f"\nCarpeta: {out_dir / 'clips'}")
    return "\n".join(log), last


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


def on_process(files, folder, recursive, skip_existing, review: dict, trims: dict, preset_name: str, *values,
               progress=gr.Progress()):
    paths = gather_sources(files, folder, recursive)
    uploaded = set(_paths(files))  # los vídeos subidos se rehacen siempre; «saltar» es para carpetas
    trims = trims or {}
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
        if skip_existing and src not in uploaded and (OUTPUT_DIR / f"{stem}_{suffix}.mp4").exists():
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
            trim = trims.get(src)
            if entry:
                res = process_video(src, preset, progress=report, stem=stem, words=_entry_words(entry),
                                    reviewed=True, hook_text=entry.get("hook") or "",
                                    metadata_text=entry.get("meta") or "", trim=trim)
            else:
                res = process_video(src, preset, progress=report, stem=stem, trim=trim)
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

    def chip(key, label):
        check(key, label, elem_classes=["cf-chip"], min_width=0, container=False)

    def note(text):
        gr.Markdown(text, elem_classes=["cf-note"])

    def section(text):
        gr.Markdown(text, elem_classes=["cf-section"])

    with gr.Blocks(title="ClipForge") as demo:
        review_state = gr.State({})
        trim_state = gr.State({})  # vídeo → [inicio, fin] del trozo elegido para el clip
        gr.HTML(header_html(__version__))

        # ================================================= 1. vídeo  ·  2. qué hacer
        moments_state = gr.State({})
        with gr.Row(equal_height=False):
            with gr.Column(scale=1, min_width=360):
                with gr.Column(elem_classes=["cf-card"]):
                    gr.HTML(step_html(1, "Sube tu vídeo", "un clip, un directo entero o varios vídeos"))
                    files = gr.File(label="Arrastra aquí tus vídeos o haz clic para elegirlos", file_count="multiple",
                                    file_types=["video"], type="filepath", elem_id="cf-upload")
                    with gr.Accordion("📁 O procesa una carpeta entera", open=False):
                        with gr.Row():
                            folder = gr.Textbox(label="Carpeta (ruta en tu PC)", placeholder=r"D:\Grabaciones\GTA", scale=3)
                            recursive = gr.Checkbox(False, label="Incluir subcarpetas")
                            skip_existing = gr.Checkbox(True, label="Saltar los ya procesados")

            with gr.Column(scale=1, min_width=360):
                with gr.Column(elem_classes=["cf-card"]):
                    gr.HTML(step_html(2, "¿Qué quieres hacer?"))
                    action = gr.Radio(ACTIONS, value=ACTION_FROM_MODE.get(v["mode"], "short"), show_label=False,
                                      elem_classes=["cf-seg", "cf-actions"])
                    action_note = gr.Markdown(elem_classes=["cf-note"])

        # ================================================= sacar momentos de un directo largo
        with gr.Column(elem_classes=["cf-card"], visible=False) as moments_card:
            gr.HTML(step_html("🔎", "Saca los mejores momentos", "de un directo de 1-8 h salen varios Shorts"))
            note("ClipForge mide tu volumen, los cambios de escena y (si lo das) el chat, transcribe los mejores "
                 "tramos y elige los clips. Revisa la tabla y pulsa **Renderizar**: cada clip sale con el juego, "
                 "los extras y el encuadre que elijas abajo.")
            with gr.Row():
                vod_path = gr.Textbox(label="Directo (vacío = el vídeo que subiste en el paso 1)",
                                      placeholder=r"C:\Users\...\Videos\2026-08-27 20-13-03.mp4", scale=4)
                pick_btn = gr.Button("📂 Elegir del PC", scale=1, min_width=150)
            note("💡 Para directos de varias horas usa **📂 Elegir del PC**: se lee directamente del disco, "
                 "sin subir ni copiar gigas.")
            with gr.Row():
                n_clips = gr.Slider(1, 15, 8, step=1, label="Clips como mucho")
                transcribe_mode = gr.Radio(
                    [("Auto", "auto"), ("Solo los mejores tramos (rápido)", "candidatos"), ("Todo el directo", "todo")],
                    value="auto", label="Qué se transcribe",
                    info="Sin GPU NVIDIA, Auto transcribe solo los tramos candidatos.")
                scenes_cb = gr.Checkbox(True, label="Detectar cambios de escena")
                moments_llm = gr.Dropdown([("Ninguna (picos de volumen)", "ninguno"), ("Claude API", "claude"),
                                           ("Ollama (local)", "ollama"), ("OpenAI API", "openai")],
                                          value=v["llm.provider"],
                                          label="IA que elige los clips")
            with gr.Accordion("💬 Chat del directo (opcional)", open=False):
                chat_file = gr.File(label="JSON de chat-downloader o .txt con la hora al principio de cada línea",
                                    file_types=[".json", ".jsonl", ".txt", ".csv"], type="filepath")
            find_btn = gr.Button("🔎 Buscar momentos", variant="primary", size="lg")
            moments_log = gr.Textbox(label="Resultado", lines=4, max_lines=10)
            moments_table = gr.Dataframe(headers=MOMENT_COLS, interactive=True, wrap=True,
                                         datatype=["bool", "number", "str", "str", "number", "number",
                                                   "str", "str", "str"],
                                         column_count=(len(MOMENT_COLS), "fixed"), label="Clips propuestos",
                                         column_widths=["5%", "4%", "11%", "11%", "6%", "6%", "17%", "19%", "21%"],
                                         elem_classes=["cf-moments"])
            render_moments_btn = gr.Button("🎬 Renderizar los marcados", variant="primary", size="lg")

        with gr.Row(equal_height=False):
            with gr.Column(scale=1, min_width=360):
                with gr.Column(elem_classes=["cf-card"]):
                    gr.HTML(step_html(3, "Juego y extras"))
                    with gr.Row():
                        preset_dd = gr.Radio(preset_choices(), value=first, label="Juego (preset)", scale=1,
                                             min_width=200, elem_classes=["cf-seg"])
                        comps["mode"] = gr.Radio([("📱 Short 9:16 · TikTok, Reels, Shorts", "short"),
                                                  ("🖥️ Largo 16:9 · YouTube", "largo")],
                                                 value=v["mode"], label="Formato", scale=2, elem_classes=["cf-seg"],
                                                 visible=False)  # lo decide «¿Qué quieres hacer?»
                    section("Qué quieres añadir")
                    with gr.Row(elem_classes=["cf-chip-row"]):
                        chip("toggles.subtitles", "💬 Subtítulos")
                        chip("toggles.highlight_keywords", "🟨 Resaltar palabras clave")
                        chip("toggles.hook_title", "🔥 Título gancho")
                        chip("toggles.cut_silences", "✂️ Cortar silencios")
                        chip("toggles.auto_zoom", "🔍 Auto-zoom")
                        chip("toggles.sfx", "🔊 Efectos de sonido")
                        chip("toggles.progress_bar", "▬ Barra de progreso")
                        chip("toggles.watermark", "©️ Marca de agua")
                        chip("toggles.export_srt", "📄 Exportar .srt")
                        chip("toggles.isolate_voice", "🎙️ Aislar voz")
                        chip("toggles.llm_correction", "🤖 Corrección con IA")
                        chip("toggles.speakers", "🗣️ Distinguir voces")
                    with gr.Accordion("💾 Guardar estos ajustes como preset", open=False):
                        with gr.Row():
                            new_name = gr.Textbox(label="Nombre (vacío = sobrescribir el preset actual)", scale=3)
                            save_btn = gr.Button("💾 Guardar preset", scale=1)
                        preset_msg = gr.Markdown()


        # ================================================= encuadre y vista previa (todo el ancho)
        with gr.Column(elem_classes=["cf-card"]) as framing_card:
            gr.HTML(step_html("🎯", "Encuadre y vista previa", "todo se coloca arrastrando · los cambios se ven al instante"))
            comps["layout.type"] = gr.Radio(
                [("🟥 Facecam esquina", "facecam_esquina"), ("⬒ Split · facecam arriba", "split"),
                 ("🌫️ Blur · juego entero", "blur"), ("🎮 Sin facecam", "sin_facecam")],
                value=v["layout.type"], label="Cómo se reparte la pantalla del Short", elem_classes=["cf-seg"])
            with gr.Row(equal_height=False):
                with gr.Column(scale=3, min_width=380):
                    section("① Tu vídeo original: qué se recorta")
                    framing = editor(elem_id="cf-editor")
                    with gr.Row():
                        autodetect_btn = gr.Button("🔎 Buscar mi webcam", size="sm")
                        reset_btn = gr.Button("↺ Encuadre automático", size="sm")
                        preview_btn = gr.Button("🔄 Actualizar todo", size="sm")
                    preview_info = gr.Markdown(elem_classes=["cf-note"])
                with gr.Column(scale=2, min_width=320):
                    section("② Así quedará: reprodúcelo y arrastra lo que quieras mover")
                    live = short_preview(elem_id="cf-live")
                    with gr.Accordion("📸 Fotograma exacto (render final)", open=False):
                        note("La vista previa en vivo es una aproximación rápida. Aquí ves un fotograma tal cual "
                             "lo dibuja el render final, en el segundo donde está la reproducción.")
                        exact_btn = gr.Button("📸 Ver fotograma exacto", size="sm")
                        out_img = gr.Image(label="Fotograma exacto", type="pil", interactive=False,
                                           elem_id="cf-result", buttons=[])
                    t_slider = gr.Slider(0, 36000, 5, step=0.01, visible=False)


        with gr.Row(equal_height=False):
            with gr.Column(scale=3, min_width=400):
                with gr.Column(elem_classes=["cf-card"]):
                    gr.HTML(step_html(4, "Ajusta los detalles", "opcional · los presets ya traen buenos valores"))
                    with gr.Tab("🎥 Facecam"):
                        note("👉 La facecam, la línea del split, el subtítulo y el gancho se **arrastran directamente "
                             "en «Así quedará»**. Haz clic en uno y usa las flechas ◀▲▼▶ y + / − para afinarlo.")
                        comps["layout.facecam_position"] = gr.Radio(
                            [("↖ Arriba izq.", "top-left"), ("⬆ Arriba centro", "top-center"),
                             ("↗ Arriba der.", "top-right"), ("✋ Donde la arrastré", "custom")],
                            value=v["layout.facecam_position"], label="Posición rápida de la facecam",
                            elem_classes=["cf-seg"])
                        with gr.Row():
                            slider("layout.facecam_radius", 0, 80, 2, "Esquinas redondeadas")
                            slider("layout.blur_strength", 0, 60, 1, "Desenfoque del fondo")
                            check("layout.facecam_shadow", "Sombra suave")
                        with gr.Accordion("🔢 Ajuste fino con números (opcional)", open=False):
                            note("Lo mismo que arrastrar, pero en números.")
                            with gr.Row():
                                slider("layout.facecam_width", 20, 100, 1, "Facecam: tamaño (% ancho)")
                                slider("layout.facecam_x", 0, 1080, 10, "Facecam: X (px)")
                                slider("layout.facecam_y", 0, 1700, 10, "Facecam: Y (px)")
                            with gr.Row():
                                slider("layout.split_ratio", 15, 70, 1, "Split: alto de la facecam (%)")
                                slider("layout.blur_fg_y", 300, 1620, 10, "Blur: altura del gameplay")
                            note("Recuadros del vídeo original (el rojo es la webcam, en % del ancho/alto):")
                            with gr.Row():
                                slider("layout.facecam_rect.0", 0, 100, 0.5, "Webcam X %")
                                slider("layout.facecam_rect.1", 0, 100, 0.5, "Webcam Y %")
                                slider("layout.facecam_rect.2", 1, 100, 0.5, "Webcam ancho %")
                                slider("layout.facecam_rect.3", 1, 100, 0.5, "Webcam alto %")
                            comps["layout.gameplay_rect"] = gr.Textbox(
                                v["layout.gameplay_rect"], label="Recuadro del gameplay [x, y, ancho, alto] · [] = automático")
                            note("Si el recuadro del gameplay está en automático se usan estos valores:")
                            with gr.Row():
                                slider("layout.gameplay_offset", -1, 1, 0.05, "Desplazamiento X")
                                slider("layout.gameplay_offset_y", -1, 1, 0.05, "Desplazamiento Y")
                                slider("layout.gameplay_zoom", 1, 3, 0.05, "Zoom")

                    with gr.Tab("💬 Subtítulos"):
                        comps["subtitles.style"] = gr.Radio(
                            [("Frase", "frase"), ("Resaltado", "resaltado"), ("Karaoke", "karaoke")],
                            value=v["subtitles.style"], label="Estilo", info="Al cambiarlo se cargan sus valores típicos",
                            elem_classes=["cf-seg"])
                        with gr.Row():
                            comps["subtitles.case"] = gr.Radio(
                                [("Original", "original"), ("minúsculas", "minusculas"), ("MAYÚSCULAS", "mayusculas")],
                                value=v["subtitles.case"], label="Mayúsculas")
                            check("subtitles.strip_punctuation", "Quitar puntos y comas")
                        note("👉 El tamaño y la altura se cambian arrastrando el subtítulo en «Así quedará» (+ / −).")
                        comps["subtitles.font"] = gr.Dropdown(font_choices(v["subtitles.font"]), value=v["subtitles.font"],
                                                              label="Fuente", allow_custom_value=True)
                        section("Colores")
                        with gr.Row():
                            color("subtitles.primary_color", "Texto")
                            color("subtitles.outline_color", "Contorno")
                            color("subtitles.highlight_color", "Palabras clave")
                            color("subtitles.active_color", "Karaoke: palabra actual")
                        with gr.Row():
                            slider("subtitles.outline", 0, 12, 1, "Grosor contorno")
                            slider("subtitles.shadow", 0, 12, 1, "Sombra")
                            slider("subtitles.fade_ms", 0, 300, 10, "Fundido (ms)")
                        section("Cuántas palabras por subtítulo")
                        with gr.Row():
                            slider("subtitles.min_words", 1, 6, 1, "Mín. palabras")
                            slider("subtitles.max_words", 1, 10, 1, "Máx. palabras")
                            slider("subtitles.max_chars", 8, 60, 1, "Máx. caracteres")
                        with gr.Accordion("🔢 Tamaño y altura con números (opcional)", open=False):
                            with gr.Row():
                                slider("subtitles.font_size", 30, 160, 2, "Tamaño")
                                slider("subtitles.pos_y", 150, 1260, 10, "Altura (centro, px)",
                                       info="Short: nunca en los 660 px de abajo")

                        section("🎙️ Reconocimiento de voz (para que ponga lo que dijiste)")
                        with gr.Row():
                            comps["transcription.dialect"] = gr.Dropdown(DIALECTS, value=v["transcription.dialect"],
                                                                         label="Acento", scale=2)
                            comps["transcription.model"] = gr.Dropdown(
                                [("large-v3-turbo · rápido", "large-v3-turbo"),
                                 ("large-v3 · más preciso (más lento)", "large-v3"),
                                 ("medium", "medium"), ("small · muy rápido", "small")],
                                value=v["transcription.model"], label="Modelo", scale=2)
                        comps["transcription.glossary"] = gr.Textbox(
                            v["transcription.glossary"], lines=2,
                            label="Palabras que debe reconocer bien (nombres, apodos, jerga del juego)",
                            placeholder="Arda Güler, Mbappé, The Finals, Vaiprin, parce, bro…")
                        note("Si las voces del juego o de tus amigos tapan la tuya, activa **🎙️ Aislar voz** en el paso 2.")

                        section("🗣️ Voces distintas")
                        note("Con **🗣️ Distinguir voces** activado (paso 2), cada persona sale con su color. "
                             "La **Voz 1** es la que más habla (normalmente tú). Desmarca una voz para no subtitularla "
                             "(p. ej. el comentarista del juego). Corrige la voz de una palabra en «Revisar texto».")
                        with gr.Row():
                            comps["transcription.speakers"] = gr.Radio(
                                [("Auto", 0), ("1", 1), ("2", 2), ("3", 3), ("4", 4)], value=v["transcription.speakers"],
                                label="¿Cuántas personas hablan?", elem_classes=["cf-seg"])
                            slider("transcription.speaker_threshold", 0.15, 0.8, 0.05, "Separar voces parecidas",
                                   info="Más bajo = separa más. Si junta a dos personas, bájalo; si parte a una, súbelo.")
                        with gr.Row():
                            for i, who in enumerate(["Voz 1 (tú)", "Voz 2", "Voz 3", "Voz 4"]):
                                with gr.Column(min_width=110):
                                    color(f"subtitles.speaker_colors.{i}", who)
                                    check(f"subtitles.speaker_show.{i}", "Mostrar")

                    with gr.Tab("🔥 Gancho"):
                        note("Texto grande que aparece al principio del Short para enganchar. Actívalo en el paso 2.")
                        comps["hook.text"] = gr.Textbox(v["hook.text"], label="Texto por defecto (cada vídeo puede tener el suyo en «Revisar texto»)",
                                                        placeholder="UNA TRAGEDIA  ·  usa | para partir en dos líneas")
                        note("👉 El tamaño y la altura se cambian arrastrando el gancho en «Así quedará» (+ / −).")
                        with gr.Row():
                            comps["hook.font"] = gr.Dropdown(font_choices(v["hook.font"]), value=v["hook.font"],
                                                             label="Fuente (se usa en cursiva)", allow_custom_value=True)
                            check("hook.uppercase", "MAYÚSCULAS")
                        with gr.Row():
                            color("hook.fill", "Relleno")
                            color("hook.outline_color", "Contorno")
                            color("hook.outer_color", "Contorno exterior")
                        with gr.Row():
                            slider("hook.outline", 0, 30, 1, "Grosor contorno")
                            slider("hook.outer", 0, 30, 1, "Grosor exterior")
                            slider("hook.tilt", -15, 15, 1, "Giro (°)")
                        slider("hook.duration", 0, 10, 0.5, "Cuánto tiempo se ve (s) · 0 = todo el clip")
                        with gr.Accordion("🔢 Tamaño y altura con números (opcional)", open=False):
                            with gr.Row():
                                slider("hook.font_size", 50, 220, 2, "Tamaño")
                                slider("hook.pos_y", 0, 1700, 10, "Altura · 0 = auto (debajo de la facecam)")

                    with gr.Tab("✨ Efectos"):
                        note("Cada efecto se enciende o apaga en el paso 2; aquí ajustas cómo se ve y suena.")
                        section("✂️ Cortar silencios")
                        note("Quita las pausas; los subtítulos se reajustan solos.")
                        with gr.Row():
                            comps["silences.source"] = gr.Radio([("Audio del micro (umbral dB)", "audio"),
                                                                 ("Huecos entre palabras", "palabras")],
                                                                value=v["silences.source"], label="Detectar con")
                            slider("silences.threshold_db", -60, -15, 1, "Umbral (dB)")
                        with gr.Row():
                            slider("silences.min_duration", 0.2, 3, 0.1, "Silencio mínimo (s)")
                            slider("silences.padding", 0, 0.5, 0.05, "Margen a cada lado (s)")
                        section("🔍 Auto-zoom")
                        with gr.Row():
                            check("zoom.on_keywords", "En palabras clave")
                            check("zoom.on_shouts", "En gritos")
                            slider("zoom.amount", 1.05, 1.5, 0.01, "Zoom (1.15 = 115 %)")
                        with gr.Row():
                            slider("zoom.shout_db", 3, 20, 1, "Grito: dB sobre lo normal")
                            slider("zoom.min_gap", 0.5, 10, 0.5, "Separación mínima (s)")
                        section("🔊 Efectos de sonido")
                        note("Pon tus propios .wav/.mp3 en `assets/sfx/`.")
                        with gr.Row():
                            comps["sfx.on_zoom"] = gr.Dropdown(sfx_choices, value=v["sfx.on_zoom"], label="Al hacer zoom")
                            comps["sfx.on_hook"] = gr.Dropdown(sfx_choices, value=v["sfx.on_hook"], label="Al salir el gancho")
                            comps["sfx.on_cut"] = gr.Dropdown(sfx_choices, value=v["sfx.on_cut"], label="En cada corte")
                            slider("sfx.volume", 0, 1.5, 0.05, "Volumen")
                        section("▬ Barra de progreso")
                        with gr.Row():
                            color("progress_bar.color", "Color")
                            slider("progress_bar.height", 2, 40, 1, "Grosor (px)")
                            comps["progress_bar.position"] = gr.Radio([("Arriba", "arriba"), ("Abajo", "abajo")],
                                                                      value=v["progress_bar.position"], label="Posición")
                        section("©️ Marca de agua")
                        with gr.Row():
                            comps["handle"] = gr.Textbox(v["handle"], label="@handle")
                            comps["watermark.position"] = gr.Dropdown(list(WATERMARK_POSITIONS), value=v["watermark.position"],
                                                                      label="Posición")
                            color("watermark.color", "Color")
                        with gr.Row():
                            slider("watermark.opacity", 10, 100, 5, "Opacidad %")
                            slider("watermark.font_size", 20, 100, 2, "Tamaño")

                    with gr.Tab("📝 Revisar texto"):
                        note("1) **Transcribe** · 2) corrige el texto de los subtítulos tal y como saldrán en pantalla "
                             "(o palabra a palabra) · 3) revisa gancho y textos · 4) **Genera**. "
                             "Los vídeos que no revises se transcriben solos al generar.")
                        with gr.Row():
                            transcribe_btn = gr.Button("📝 Transcribir para revisar", variant="secondary")
                            video_dd = gr.Dropdown([], label="Vídeo", interactive=True)
                        review_msg = gr.Markdown()
                        with gr.Row():
                            hook_box = gr.Textbox(label="Título gancho de este vídeo", placeholder="UNA TRAGEDIA", scale=2)
                            regroup_btn = gr.Button("↻ Reagrupar con el estilo actual", size="sm", scale=1)
                            llm_btn = gr.Button("🤖 Pasar por la IA", size="sm", scale=1)
                        with gr.Tab("Subtítulos (frases)"):
                            note("Cada fila es un subtítulo tal y como aparece. Cambia el texto para corregir errores "
                                 "(si mantienes el nº de palabras, el karaoke conserva los tiempos exactos), ajusta "
                                 "inicio/fin o **vacía el texto para borrar** el subtítulo.")
                            phrase_table = gr.Dataframe(headers=PHRASE_HEADERS, datatype=["number", "number", "str"],
                                                        type="array", interactive=True, column_count=(3, "fixed"),
                                                        label="Subtítulos (segundos)", max_height=520, wrap=True)
                        with gr.Tab("Palabras"):
                            note("Ajuste fino: texto y tiempos de cada palabra, qué se **resalta**, a qué **frase** "
                                 "(nº de subtítulo) pertenece y qué **voz** la dice (1, 2, 3… con «Distinguir voces»).")
                            word_table = gr.Dataframe(headers=WORD_HEADERS,
                                                      datatype=["str", "number", "number", "bool", "number", "number"],
                                                      type="array", interactive=True, column_count=(6, "fixed"),
                                                      label="Palabras (segundos)", max_height=520)
                        meta_box = gr.Textbox(label="Títulos, descripción y hashtags (IA; editable, va a _meta.txt)", lines=6)

                    with gr.Tab("⚙️ Avanzado"):
                        with gr.Accordion("🤖 IA (LLM)", open=False):
                            note("Corrige la transcripción, elige palabras clave, propone el gancho y escribe títulos, "
                                 "descripción y hashtags. Activa también **Corrección con IA** en el paso 2. "
                                 "Claves en `.env` (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`); Ollama en `OLLAMA_HOST`.")
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

                        with gr.Accordion("🎬 Motor de render", open=False):
                            ok, why = remotion_render.status()
                            note("**FFmpeg** es rápido y vale para todo. **Remotion** (solo Shorts) dibuja los subtítulos, "
                                 "el gancho y el zoom con animaciones más suaves, pero tarda bastante más.\n\n"
                                 + ("✅ " if ok else "⚠️ ") + why)
                            with gr.Row():
                                comps["render.engine"] = gr.Radio([("FFmpeg (rápido)", "ffmpeg"),
                                                                   ("Remotion (animaciones avanzadas)", "remotion")],
                                                                  value=v["render.engine"], label="Motor para los Shorts")
                                comps["subtitles.animation"] = gr.Radio([("Pop", "pop"), ("Deslizar", "slide"),
                                                                         ("Fundido", "fade"), ("Ninguna", "none")],
                                                                        value=v["subtitles.animation"],
                                                                        label="Entrada de cada frase (Remotion)")
                            with gr.Row():
                                comps["render.encoder"] = gr.Dropdown([("Auto (GPU NVIDIA/AMD si hay)", "auto"), ("NVENC (NVIDIA)", "nvenc"),
                                                                       ("AMF (AMD)", "amf"), ("CPU x264", "x264")],
                                                                      value=v["render.encoder"], label="Encoder")
                                comps["render.long_resolution"] = gr.Dropdown([("Original", "original"), ("1080p", "1080p")],
                                                                              value=v["render.long_resolution"], label="Resolución Modo Largo")

                        with gr.Accordion("🎙️ Transcripción y audio", open=False):
                            note("El acento, el modelo y las palabras clave están en la pestaña **💬 Subtítulos**.")
                            with gr.Row():
                                comps["transcription.device"] = gr.Dropdown([("Auto", "auto"), ("GPU (CUDA)", "cuda"), ("CPU", "cpu")],
                                                                            value=v["transcription.device"], label="Dispositivo")
                                check("transcription.reuse_cache", "Reusar transcripción si ya existe")
                            with gr.Row():
                                comps["audio.transcribe_track"] = gr.Number(v["audio.transcribe_track"], precision=0,
                                                                            label="Pista del micro (transcribir, silencios, gritos)")
                                comps["audio.output_track"] = gr.Dropdown(["0", "1", "2", "3", "mix"], value=v["audio.output_track"],
                                                                          label="Pista de audio del vídeo final", allow_custom_value=True)

            with gr.Column(scale=2, min_width=360):
                with gr.Column(elem_classes=["cf-card"]) as generate_card:
                    generate_title = gr.HTML(step_html(5, "Genera tus clips"))
                    process_btn = gr.Button("🚀 Generar clips", variant="primary", size="lg", elem_id="cf-go")
                    log_box = gr.Textbox(label="Progreso", lines=5, max_lines=10, autoscroll=True, elem_id="cf-log")
                    files_out = gr.Files(label="Archivos de esta tanda", visible=False)

                with gr.Column(elem_classes=["cf-card"]):
                    gr.HTML(step_html("🎬", "Tus clips", "míralos aquí sin descargarlos"))
                    with gr.Row():
                        clip_dd = gr.Dropdown(list_clips(), value=None, label="Clip", scale=4)
                        refresh_clips_btn = gr.Button("🔄", size="sm", scale=0, min_width=50)
                    video_out = gr.Video(label="Reproductor", elem_id="cf-player", autoplay=True, buttons=["download"])
                    with gr.Row():
                        open_btn = gr.Button("📂 Abrir carpeta de clips", size="sm")
                    open_msg = gr.Markdown(elem_classes=["cf-note"])

        inputs = [comps[k] for k in KEYS]
        rect_sliders = [comps[f"layout.facecam_rect.{i}"] for i in range(4)]
        preview_in = [files, preset_dd, t_slider, review_state, trim_state, *inputs]
        preview_out = [framing, live, preview_info]
        review_out = [review_state, phrase_table, word_table, hook_box, meta_box, review_msg]
        live_refresh = dict(fn=on_live_refresh, inputs=preview_in, outputs=live, trigger_mode="always_last",
                            show_progress="hidden")

        preset_dd.change(on_load_preset, preset_dd, inputs).then(on_auto_preview, preview_in, preview_out)
        save_btn.click(on_save_preset, [preset_dd, new_name, *inputs], [preset_dd, preset_msg])
        comps["subtitles.style"].input(on_style_input, comps["subtitles.style"], [comps[k] for k in STYLE_KEYS]).then(
            **live_refresh)
        preview_btn.click(on_preview, preview_in, preview_out)
        comps["layout.type"].input(on_auto_preview, preview_in, preview_out)
        comps["mode"].input(on_auto_preview, preview_in, preview_out)
        files.upload(on_upload_detect, [files, comps["layout.type"]], [*rect_sliders, preview_info]).then(
            on_auto_preview, preview_in, preview_out)
        files.clear(lambda: ("{}", "{}", ""), None, preview_out)
        framing.input(on_editor_input, [framing, *preview_in],
                      [*rect_sliders, comps["layout.gameplay_rect"], live, preview_info])
        live.input(on_short_input, [live, *preview_in],
                   [*[comps[k] for k in SHORT_EDIT_KEYS], trim_state, t_slider, live], show_progress="hidden")
        exact_btn.click(on_exact_frame, preview_in, out_img)
        autodetect_btn.click(on_autodetect, files, [*rect_sliders, preview_info]).success(
            on_preview, preview_in, preview_out)
        reset_btn.click(on_reset_framing, None, comps["layout.gameplay_rect"]).then(
            on_auto_preview, preview_in, preview_out)
        # Cualquier otro ajuste cambiado a mano: la vista previa en vivo se redibuja al momento.
        skip = {"layout.type", "mode", "subtitles.style"}
        gr.on([comps[k].input for k in KEYS if k not in skip], **live_refresh)
        clip_dd.input(on_select_clip, clip_dd, video_out)
        refresh_clips_btn.click(lambda: on_refresh_clips(False)[0], None, clip_dd)
        open_btn.click(on_open_output, None, open_msg)
        demo.load(lambda: on_refresh_clips(False)[0], None, clip_dd)

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
        process_btn.click(on_process, [files, folder, recursive, skip_existing, review_state, trim_state, preset_dd, *inputs],
                          [log_box, files_out, video_out]).then(on_refresh_clips, None, [clip_dd, video_out])
        action_out = [comps["mode"], moments_card, framing_card, generate_card, process_btn, action_note]
        action.input(on_action, action, action_out).then(on_auto_preview, preview_in, preview_out)
        comps["mode"].change(on_mode_sync, [comps["mode"], action], action, show_progress="hidden").then(
            on_action_keep_note, action, action_out[:-1], show_progress="hidden")
        files.upload(on_suggest_action, [files, action], [action, action_note]).then(
            on_action_keep_note, action, action_out[:-1])
        pick_btn.click(on_pick_vod, vod_path, vod_path)
        demo.load(on_action, action, action_out)
        find_btn.click(on_find_moments, [vod_path, files, chat_file, n_clips, transcribe_mode, scenes_cb, moments_llm,
                                         preset_dd, *inputs], [moments_state, moments_table, moments_log])
        render_moments_btn.click(on_render_moments, [moments_state, moments_table, preset_dd, *inputs],
                                 [moments_log, video_out]).then(on_refresh_clips, None, [clip_dd, video_out])
    return demo


if __name__ == "__main__":
    port = int(os.environ.get("CLIPFORGE_PORT", "7860"))
    build_ui().queue().launch(
        server_name="127.0.0.1",
        server_port=port,
        inbrowser=os.environ.get("CLIPFORGE_NO_BROWSER") != "1",
        allowed_paths=[str(OUTPUT_DIR), str(FONTS_DIR)],
        theme=build_theme(),
        css=CSS,
        js=JS,
    )
