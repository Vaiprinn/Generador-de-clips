"""Presets (JSON en /presets): un perfil por juego/canal.

Cada preset guarda el modo, el layout, el estilo de subtítulos, la transcripción,
la pista de audio y el estado de todos los interruptores. Los campos que falten en
un JSON se rellenan con DEFAULTS, así que los presets antiguos siguen funcionando.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .paths import PRESETS_DIR

DEFAULTS: dict = {
    "name": "default",
    "mode": "short",  # "short" (9:16) | "largo" (16:9, sin facecam ni gancho)
    "handle": "",
    "layout": {
        "type": "facecam_esquina",  # facecam_esquina | split | blur | sin_facecam
        # Rectángulo de la facecam en el vídeo ORIGINAL, en fracciones [x, y, ancho, alto].
        "facecam_rect": [0.0, 0.0, 0.25, 0.25],
        "facecam_position": "top-right",  # top-right | top-left | top-center
        "facecam_width": 0.60,  # fracción del ancho de salida (1080)
        "facecam_y": 200,  # px desde arriba en la salida
        "facecam_margin": 40,  # px al borde lateral
        "facecam_radius": 28,
        "facecam_shadow": True,
        # Recorte 9:16 del gameplay: -1 = todo a la izquierda, 0 = centro, 1 = derecha.
        "gameplay_offset": 0.0,
        "gameplay_offset_y": 0.0,  # -1 arriba · 0 centro · 1 abajo (solo con zoom)
        "gameplay_zoom": 1.0,
        "split_ratio": 0.42,  # split: alto de la facecam (fracción de 1920)
        "blur_strength": 20,  # blur: intensidad del desenfoque del fondo
        "blur_fg_y": 960,  # blur: centro vertical del gameplay 16:9
    },
    "subtitles": {
        "style": "frase",  # frase | resaltado | karaoke
        "font": "Nunito Black",
        "font_size": 72,
        "primary_color": "#FFFFFF",
        "outline_color": "#000000",
        "shadow_color": "#000000",
        "outline": 4,
        "shadow": 3,
        "case": "original",  # original | minusculas | mayusculas
        "strip_punctuation": True,
        "min_words": 3,
        "max_words": 5,
        "max_chars": 24,
        "pos_y": 1100,  # centro vertical del subtítulo en la salida 1080x1920
        "fade_ms": 60,
        "highlight_color": "#FFE14D",  # palabras clave
        "active_color": "#39E75F",  # karaoke: palabra que se está diciendo
        "pop_scale": 115,  # karaoke: % del "pop"
    },
    # Título gancho (solo Modo Short). El texto se escribe por vídeo en la revisión.
    "hook": {
        "text": "",
        "font": "Montserrat Black",
        "font_size": 120,
        "fill": "#FFFFFF",
        "outline_color": "#E3262E",
        "outline": 12,
        "outer_color": "#FFFFFF",
        "outer": 10,
        "uppercase": True,
        "duration": 2.5,  # segundos; 0 = todo el clip
        "pos_y": 0,  # 0 = automático (debajo de la facecam)
        "tilt": 0,  # grados de giro
    },
    "transcription": {
        "model": "large-v3-turbo",
        "language": "es",
        "device": "auto",  # auto | cuda | cpu
        "glossary": "",
        "reuse_cache": True,
    },
    "audio": {
        "transcribe_track": 0,  # pista con el micro (0 = primera)
        "output_track": "0",  # índice de pista o "mix" (mezcla todas)
    },
    "toggles": {
        "subtitles": True,
        "hook_title": False,
        "auto_emojis": False,
        "highlight_keywords": False,
        "cut_silences": False,
        "auto_zoom": False,
        "sfx": False,
        "progress_bar": False,
        "watermark": False,
        "isolate_voice": False,
        "llm_correction": False,
        "export_srt": True,
    },
    "render": {
        "encoder": "auto",  # auto | nvenc | x264
        "crf": 20,
        "long_resolution": "original",  # Modo Largo: "original" | "1080p"
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w\-]+", "_", name.strip().lower()).strip("_")
    if not name:
        raise ValueError("Nombre de preset vacío")
    return name


def list_presets(directory: Path = PRESETS_DIR) -> list[str]:
    return sorted(p.stem for p in directory.glob("*.json"))


def load_preset(name: str, directory: Path = PRESETS_DIR) -> dict:
    path = directory / f"{_safe_name(name)}.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    preset = deep_merge(DEFAULTS, data)
    preset["name"] = path.stem
    return preset


def save_preset(preset: dict, name: str | None = None, directory: Path = PRESETS_DIR) -> Path:
    name = _safe_name(name or preset.get("name", "default"))
    directory.mkdir(parents=True, exist_ok=True)
    data = deep_merge(DEFAULTS, preset)
    data["name"] = name
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def get_path(d: dict, dotted: str):
    for part in dotted.split("."):
        d = d[part]
    return d


def set_path(d: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value
