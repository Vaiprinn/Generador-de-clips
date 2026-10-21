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
from .keywords import auto_highlight
from .layout import OUT_H, OUT_W, build_long_filter, build_short_filter, compute_short_layout
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


def is_short(preset: dict) -> bool:
    return preset.get("mode", "short") != "largo"


def transcribe_video(
    src: str | Path,
    preset: dict,
    progress: ProgressFn | None = None,
    timer: StepTimer | None = None,
    info: media.MediaInfo | None = None,
) -> list[Word]:
    """Extrae la pista del micro, transcribe (con caché) y sugiere palabras clave."""
    ensure_dirs()
    report = progress or (lambda f, m: None)
    timer = timer or StepTimer()
    src = Path(src)
    info = info or media.probe(src)
    if not info.audio_tracks:
        raise ValueError("El vídeo no tiene audio que transcribir.")
    track = int(preset["audio"].get("transcribe_track", 0) or 0)
    track = track if 0 <= track < len(info.audio_tracks) else 0
    work = Path(tempfile.mkdtemp(prefix="asr_", dir=WORK_DIR))
    try:
        def make_wav() -> Path:
            report(0.0, "Extrayendo audio…")
            with timer.step("2. Extraer audio"):
                return media.extract_audio(src, work / "audio.wav", track)

        with timer.step("3. Transcribir (faster-whisper)"):
            words, device = transcribe_cached(src, make_wav, track, preset["transcription"], info.duration, report)
        auto_highlight(words, preset["transcription"].get("glossary", ""))
        report(1.0, f"Transcripción lista ({len(words)} palabras, {device}).")
        return words
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _hook_cfg(preset: dict, hook_text: str | None, layout_hook_y: int) -> dict | None:
    if not is_short(preset) or not preset["toggles"].get("hook_title", False):
        return None
    hook = dict(preset["hook"])
    if hook_text is not None:
        hook["text"] = hook_text
    if not (hook.get("text") or "").strip():
        return None
    if not int(hook.get("pos_y") or 0):
        hook["pos_y"] = layout_hook_y
    return hook


def process_video(
    src: str | Path,
    preset: dict,
    progress: ProgressFn | None = None,
    words: list[Word] | None = None,
    output_dir: Path = OUTPUT_DIR,
    stem: str | None = None,
    reviewed: bool = False,
    hook_text: str | None = None,
) -> JobResult:
    """Procesa un vídeo (Modo Short o Largo).

    `words` permite saltarse la transcripción (p. ej. la tabla de revisión). Si
    `reviewed` es True se respetan sus marcas de resaltado tal cual.
    """
    ensure_dirs()
    output_dir.mkdir(parents=True, exist_ok=True)
    report = progress or (lambda f, m: None)
    src = Path(src)
    stem = stem or safe_stem(src)
    timer = StepTimer()
    toggles = preset["toggles"]
    sub_cfg = preset["subtitles"]
    short = is_short(preset)
    need_words = toggles.get("subtitles", True) or toggles.get("export_srt", True)

    work = Path(tempfile.mkdtemp(prefix=f"{stem}_", dir=WORK_DIR))
    try:
        report(0.0, "Analizando vídeo…")
        with timer.step("1. Analizar vídeo (ffprobe)"):
            info = media.probe(src)

        if words is None:
            if need_words:
                lo, hi = _W_TRANSCRIBE
                words = transcribe_video(src, preset, lambda f, m: report(lo + (hi - lo) * f, m), timer, info)
            else:
                words = []
        elif not reviewed:
            auto_highlight(words, preset["transcription"].get("glossary", ""))
        words = [w for w in words if w.text.strip()]

        if short:
            layout = compute_short_layout(info.width, info.height, preset["layout"])
            out_w, out_h = OUT_W, OUT_H
            hook = _hook_cfg(preset, hook_text, layout.hook_y)
        else:
            layout = None
            fg_size = build_long_filter(info.width, info.height, preset["render"].get("long_resolution", "original"))[1]
            out_w, out_h = fg_size
            hook = None

        report(0.56, "Generando subtítulos…")
        files: list[Path] = []
        with timer.step("4. Subtítulos (.ass/.srt)"):
            ass_out = output_dir / f"{stem}.ass"
            ass = subtitles.build_ass(
                words, sub_cfg, out_w, out_h, short=short,
                highlight=toggles.get("highlight_keywords", False),
                hook=hook, duration=info.duration,
                show_subtitles=toggles.get("subtitles", True),
            )
            subtitles.write_text(ass_out, ass)
            files.append(ass_out)
            if toggles.get("export_srt", True):
                srt_out = output_dir / f"{stem}.srt"
                subtitles.write_text(srt_out, subtitles.build_srt(words))
                files.append(srt_out)
            words_out = output_dir / f"{stem}_words.json"
            words_out.write_text(
                json.dumps(words_to_json(words), ensure_ascii=False, indent=1), encoding="utf-8"
            )
            files.append(words_out)
        burn = toggles.get("subtitles", True) or hook is not None

        report(_W_RENDER[0], "Renderizando…")
        out_video = output_dir / f"{stem}_{'short' if short else 'largo'}.mp4"
        with timer.step("5. Render (FFmpeg)"):
            if short:
                fg = build_short_filter(layout, preset["layout"], work,
                                        ass_path=ass_out if burn else None, fonts_dir=FONTS_DIR)
            else:
                fg, _ = build_long_filter(info.width, info.height, preset["render"].get("long_resolution", "original"),
                                          ass_path=ass_out if burn else None, fonts_dir=FONTS_DIR)
            cmd = build_command(info, fg, out_video, preset["render"], str(preset["audio"]["output_track"]))
            lo, hi = _W_RENDER
            run_ffmpeg(
                cmd, info.duration, output_dir / f"{stem}_ffmpeg.log",
                lambda f: report(lo + (hi - lo) * f, f"Renderizando… {f * 100:.0f} %"),
            )

        encoder = "h264_nvenc" if "h264_nvenc" in cmd else "libx264"
        n_hl = sum(1 for w in words if w.hl)
        mode_txt = f"Short | Layout: {preset['layout']['type']}" if short else "Largo"
        summary = (
            f"ClipForge — {src.name}\n"
            f"Preset: {preset.get('name')}  |  Modo: {mode_txt}  |  Estilo: {sub_cfg.get('style')}\n"
            f"Origen: {info.width}x{info.height} @ {float(info.fps):.2f} fps, {info.duration:.1f} s, "
            f"{len(info.audio_tracks)} pista(s) de audio\n"
            f"Salida: {out_w}x{out_h}, {encoder}\n"
            f"Palabras: {len(words)}  |  Clave: {n_hl}  |  Gancho: {hook['text'] if hook else '—'}"
            f"{'  |  revisado a mano' if reviewed else ''}\n\n"
            f"Tiempos:\n{timer.summary()}\n"
        )
        meta = output_dir / f"{stem}_meta.txt"
        meta.write_text(summary, encoding="utf-8")
        files = [out_video, *files, meta]
        report(1.0, "¡Listo!")
        return JobResult(out_video, files, timer.as_dict(), summary)
    finally:
        shutil.rmtree(work, ignore_errors=True)


_SAMPLE = "qué locura de partida chicos"


def render_preview_frame(
    src: str | Path, preset: dict, t: float, out_png: Path, hook_text: str | None = None
) -> Path:
    """Renderiza un único fotograma con el layout, un subtítulo de ejemplo y el gancho."""
    ensure_dirs()
    info = media.probe(src)
    work = Path(tempfile.mkdtemp(prefix="preview_", dir=WORK_DIR))
    try:
        short = is_short(preset)
        sample = [Word(w, i * 0.4, i * 0.4 + 0.35, hl=(w == "locura")) for i, w in enumerate(_SAMPLE.split())]
        # Solo la primera frase del estilo elegido, visible desde t=0.
        sample = subtitles.group_phrases(
            sample, int(preset["subtitles"].get("min_words", 3)),
            int(preset["subtitles"].get("max_words", 5)), int(preset["subtitles"].get("max_chars", 24)),
        )[0]
        for w in sample:
            w.start, w.end = 0.0, 30.0
        if short:
            layout = compute_short_layout(info.width, info.height, preset["layout"])
            out_w, out_h = OUT_W, OUT_H
            hook_default = hook_text if hook_text is not None else (preset["hook"].get("text") or "una tragedia")
            hook = _hook_cfg(preset, hook_default, layout.hook_y)
        else:
            out_w, out_h = build_long_filter(info.width, info.height, preset["render"].get("long_resolution", "original"))[1]
            hook = None
        ass = subtitles.write_text(work / "preview.ass", subtitles.build_ass(
            sample, preset["subtitles"], out_w, out_h, short=short,
            highlight=preset["toggles"].get("highlight_keywords", False),
            hook=hook, animate=False, show_subtitles=preset["toggles"].get("subtitles", True),
        ))
        if short:
            fg = build_short_filter(layout, preset["layout"], work, ass_path=ass, fonts_dir=FONTS_DIR)
        else:
            fg, _ = build_long_filter(info.width, info.height, preset["render"].get("long_resolution", "original"),
                                      ass_path=ass, fonts_dir=FONTS_DIR)
        t = max(0.0, min(t, max(info.duration - 0.2, 0.0)))
        cmd = build_command(info, fg, out_png, preset["render"], seek=t, frames=1)
        media.run(cmd)
        return out_png
    finally:
        shutil.rmtree(work, ignore_errors=True)
