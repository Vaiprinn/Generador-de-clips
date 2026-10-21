"""Orquestación de un vídeo: probe → audio → transcripción → subtítulos → render."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import media, subtitles
from .layout import OUT_H, OUT_W, build_short_filter, compute_short_layout
from .paths import FONTS_DIR, OUTPUT_DIR, WORK_DIR, ensure_dirs
from .render import build_command, run_ffmpeg
from .timing import StepTimer
from .transcribe import Word, transcribe_cached, words_to_json

# progress(fracción 0-1 del vídeo actual, mensaje)
ProgressFn = Callable[[float, str], None]

# Peso aproximado de cada paso en la barra de progreso.
_W_TRANSCRIBE = (0.05, 0.55)
_W_RENDER = (0.60, 0.99)


@dataclass
class JobResult:
    video: Path
    files: list[Path] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    summary: str = ""


def safe_stem(path: str | Path) -> str:
    stem = re.sub(r"[^\w\-]+", "_", Path(path).stem).strip("_")
    return stem or "clip"


def process_video(
    src: str | Path,
    preset: dict,
    progress: ProgressFn | None = None,
    words: list[Word] | None = None,
    output_dir: Path = OUTPUT_DIR,
    stem: str | None = None,
) -> JobResult:
    """Procesa un vídeo en Modo Short. `words` permite saltarse la transcripción."""
    ensure_dirs()
    output_dir.mkdir(parents=True, exist_ok=True)
    report = progress or (lambda f, m: None)
    src = Path(src)
    stem = stem or safe_stem(src)
    timer = StepTimer()
    toggles = preset["toggles"]
    sub_cfg = preset["subtitles"]

    if preset.get("mode", "short") != "short":
        raise NotImplementedError("El Modo Largo llega en la Fase 2.")

    work = Path(tempfile.mkdtemp(prefix=f"{stem}_", dir=WORK_DIR))
    try:
        report(0.0, "Analizando vídeo…")
        with timer.step("1. Analizar vídeo (ffprobe)"):
            info = media.probe(src)

        if toggles.get("subtitles", True) or toggles.get("export_srt", True):
            if words is None:
                track = int(preset["audio"].get("transcribe_track", 0))
                if not info.audio_tracks:
                    raise ValueError("El vídeo no tiene audio que transcribir.")
                track = track if track < len(info.audio_tracks) else 0
                wav_holder: dict[str, Path] = {}

                def make_wav() -> Path:
                    report(0.02, "Extrayendo audio…")
                    with timer.step("2. Extraer audio"):
                        wav_holder["p"] = media.extract_audio(src, work / "audio.wav", track)
                    return wav_holder["p"]

                lo, hi = _W_TRANSCRIBE
                with timer.step("3. Transcribir (faster-whisper)"):
                    words, device = transcribe_cached(
                        src, make_wav, track, preset["transcription"], info.duration,
                        lambda f, m: report(lo + (hi - lo) * f, m),
                    )
                report(hi, f"Transcripción lista ({len(words)} palabras, {device}).")
        else:
            words = []

        report(0.56, "Generando subtítulos…")
        files: list[Path] = []
        with timer.step("4. Subtítulos (.ass/.srt)"):
            phrases = subtitles.build_phrases(words, sub_cfg)
            ass_out = output_dir / f"{stem}.ass"
            subtitles.write_text(ass_out, subtitles.build_ass(phrases, sub_cfg, OUT_W, OUT_H))
            files.append(ass_out)
            if toggles.get("export_srt", True):
                srt_out = output_dir / f"{stem}.srt"
                subtitles.write_text(srt_out, subtitles.build_srt(phrases, sub_cfg))
                files.append(srt_out)
            words_out = output_dir / f"{stem}_words.json"
            words_out.write_text(
                json.dumps(words_to_json(words), ensure_ascii=False, indent=1), encoding="utf-8"
            )
            files.append(words_out)

        report(_W_RENDER[0], "Renderizando…")
        out_video = output_dir / f"{stem}_short.mp4"
        with timer.step("5. Render (FFmpeg)"):
            layout = compute_short_layout(info.width, info.height, preset["layout"])
            fg = build_short_filter(
                layout, preset["layout"], work,
                ass_path=ass_out if toggles.get("subtitles", True) else None,
                fonts_dir=FONTS_DIR,
            )
            cmd = build_command(info, fg, out_video, preset["render"], str(preset["audio"]["output_track"]))
            lo, hi = _W_RENDER
            run_ffmpeg(
                cmd, info.duration, output_dir / f"{stem}_ffmpeg.log",
                lambda f: report(lo + (hi - lo) * f, f"Renderizando… {f * 100:.0f} %"),
            )

        encoder = "h264_nvenc" if "h264_nvenc" in cmd else "libx264"
        summary = (
            f"ClipForge — {src.name}\n"
            f"Preset: {preset.get('name')}  |  Modo: Short  |  Layout: {preset['layout']['type']}\n"
            f"Origen: {info.width}x{info.height} @ {float(info.fps):.2f} fps, {info.duration:.1f} s, "
            f"{len(info.audio_tracks)} pista(s) de audio\n"
            f"Salida: {OUT_W}x{OUT_H}, {encoder}\n"
            f"Palabras: {len(words)}  |  Frases: {len(phrases)}\n\n"
            f"Tiempos:\n{timer.summary()}\n"
        )
        meta = output_dir / f"{stem}_meta.txt"
        meta.write_text(summary, encoding="utf-8")
        files = [out_video, *files, meta]
        report(1.0, "¡Listo!")
        return JobResult(out_video, files, timer.as_dict(), summary)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render_preview_frame(src: str | Path, preset: dict, t: float, out_png: Path) -> Path:
    """Renderiza un único fotograma con el layout y un subtítulo de ejemplo."""
    ensure_dirs()
    info = media.probe(src)
    work = Path(tempfile.mkdtemp(prefix="preview_", dir=WORK_DIR))
    try:
        sub_cfg = preset["subtitles"]
        ass = None
        if preset["toggles"].get("subtitles", True):
            sample = [Word(w, 0.0, 30.0) for w in "así se verán tus subtítulos".split()]
            phrase = subtitles.Phrase(sample, 0.0, 30.0)
            ass = subtitles.write_text(work / "preview.ass", subtitles.build_ass([phrase], sub_cfg))
        layout = compute_short_layout(info.width, info.height, preset["layout"])
        fg = build_short_filter(layout, preset["layout"], work, ass_path=ass, fonts_dir=FONTS_DIR)
        t = max(0.0, min(t, max(info.duration - 0.1, 0.0)))
        cmd = build_command(info, fg, out_png, preset["render"], seek=t, frames=1)
        media.run(cmd)
        return out_png
    finally:
        shutil.rmtree(work, ignore_errors=True)
