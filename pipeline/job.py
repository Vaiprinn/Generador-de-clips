"""Orquestación de un vídeo: probe → audio → transcripción → (LLM) → efectos → subtítulos → render."""
from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import llm, media, sfx, silence, subtitles
from .effects import shout_indices, word_loudness_db, zoom_expr, zoom_times
from .keywords import auto_highlight
from .layout import OUT_H, OUT_W, FilterOptions, build_long_filter, build_short_filter, compute_short_layout
from .paths import FONTS_DIR, OUTPUT_DIR, WORK_DIR, ensure_dirs
from .render import build_command, run_ffmpeg
from .timing import StepTimer
from .transcribe import Word, transcribe_cached, words_to_json

log = logging.getLogger(__name__)

# progress(fracción 0-1 del vídeo actual, mensaje)
ProgressFn = Callable[[float, str], None]

# Peso aproximado de cada paso en la barra de progreso.
_W_TRANSCRIBE = (0.05, 0.50)
_W_LLM = (0.50, 0.58)
_W_RENDER = (0.62, 0.99)


@dataclass
class JobResult:
    video: Path
    files: list[Path] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    summary: str = ""
    metadata: str = ""  # títulos/descripción/hashtags del LLM
    stats: dict = field(default_factory=dict)


def safe_stem(path: str | Path) -> str:
    stem = re.sub(r"[^\w\-]+", "_", Path(path).stem).strip("_")
    return stem or "clip"


def is_short(preset: dict) -> bool:
    return preset.get("mode", "short") != "largo"


def mic_track(preset: dict, info: media.MediaInfo) -> int:
    track = int(preset["audio"].get("transcribe_track", 0) or 0)
    return track if 0 <= track < len(info.audio_tracks) else 0


def transcribe_video(
    src: str | Path,
    preset: dict,
    progress: ProgressFn | None = None,
    timer: StepTimer | None = None,
    info: media.MediaInfo | None = None,
    notes: list[str] | None = None,
) -> list[Word]:
    """Extrae la pista del micro (y aísla la voz si se pide), transcribe con caché y sugiere palabras clave."""
    ensure_dirs()
    report = progress or (lambda f, m: None)
    timer = timer or StepTimer()
    notes = notes if notes is not None else []
    src = Path(src)
    info = info or media.probe(src)
    if not info.audio_tracks:
        raise ValueError("El vídeo no tiene audio que transcribir.")
    track = mic_track(preset, info)
    isolate = bool(preset["toggles"].get("isolate_voice", False))
    cfg = dict(preset["transcription"], isolate=isolate)
    work = Path(tempfile.mkdtemp(prefix="asr_", dir=WORK_DIR))
    try:
        def make_wav() -> Path:
            report(0.0, "Extrayendo audio…")
            with timer.step("2. Extraer audio"):
                wav = media.extract_audio(src, work / "audio.wav", track)
            if isolate:
                from .voice import DemucsUnavailable, isolate_vocals

                report(0.02, "Aislando la voz (Demucs)…")
                try:
                    with timer.step("2b. Aislar voz (Demucs)"):
                        wav = isolate_vocals(wav, work, cfg.get("device", "auto"))
                except (DemucsUnavailable, RuntimeError) as e:
                    log.warning("Demucs: %s", e)
                    notes.append(f"Aislar voz desactivado: {e}")
            return wav

        with timer.step("3. Transcribir (faster-whisper)"):
            words, device = transcribe_cached(src, make_wav, track, cfg, info.duration, report)
        auto_highlight(words, preset["transcription"].get("glossary", ""))
        report(1.0, f"Transcripción lista ({len(words)} palabras, {device}).")
        return words
    finally:
        shutil.rmtree(work, ignore_errors=True)


def llm_enabled(preset: dict) -> bool:
    return bool(preset["toggles"].get("llm_correction")) and preset["llm"].get("provider", "ninguno") != "ninguno"


def enrich_with_llm(words: list[Word], preset: dict, progress: ProgressFn | None = None) -> llm.LLMResult | None:
    """Pasa las palabras por el LLM elegido y aplica correcciones/palabras clave."""
    if not llm_enabled(preset) or not words:
        return None
    res = llm.run_llm(
        words, preset["llm"], game=preset.get("name", ""),
        glossary=preset["transcription"].get("glossary", ""), short=is_short(preset), progress=progress,
    )
    llm.apply_llm(words, res, preset["llm"])
    return res


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


def _watermark_cfg(preset: dict) -> dict | None:
    handle = (preset.get("handle") or "").strip()
    if not preset["toggles"].get("watermark") or not handle:
        return None
    return dict(preset["watermark"], text=handle)


def process_video(
    src: str | Path,
    preset: dict,
    progress: ProgressFn | None = None,
    words: list[Word] | None = None,
    output_dir: Path = OUTPUT_DIR,
    stem: str | None = None,
    reviewed: bool = False,
    hook_text: str | None = None,
    llm_result: llm.LLMResult | None = None,
    metadata_text: str | None = None,
) -> JobResult:
    """Procesa un vídeo (Modo Short o Largo).

    `words` permite saltarse la transcripción (p. ej. la tabla de revisión). Si
    `reviewed` es True se respetan tal cual sus textos, frases y resaltados.
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
    need_words = toggles.get("subtitles", True) or toggles.get("export_srt", True) or toggles.get("auto_zoom")
    notes: list[str] = []

    work = Path(tempfile.mkdtemp(prefix=f"{stem}_", dir=WORK_DIR))
    try:
        report(0.0, "Analizando vídeo…")
        with timer.step("1. Analizar vídeo (ffprobe)"):
            info = media.probe(src)

        if words is None:
            if need_words:
                lo, hi = _W_TRANSCRIBE
                words = transcribe_video(src, preset, lambda f, m: report(lo + (hi - lo) * f, m), timer, info, notes)
            else:
                words = []
        elif not reviewed:
            auto_highlight(words, preset["transcription"].get("glossary", ""))
        words = [w for w in words if w.text.strip()]

        if not reviewed and llm_result is None and llm_enabled(preset) and words:
            lo, hi = _W_LLM
            try:
                with timer.step("3b. LLM"):
                    llm_result = enrich_with_llm(words, preset, lambda f, m: report(lo + (hi - lo) * f, m))
            except Exception as e:  # el LLM es opcional: si falla, se sigue sin él
                log.warning("LLM: %s", e)
                notes.append(f"LLM no disponible: {e}")
        if hook_text is None and llm_result and preset["llm"].get("propose_hook", True) and llm_result.hook:
            hook_text = llm_result.hook

        # ---- corte de silencios
        segs = None
        if toggles.get("cut_silences"):
            sc = preset["silences"]
            with timer.step("4a. Detectar silencios"):
                if sc.get("source") == "palabras":
                    sil = silence.silences_from_words(words, info.duration, float(sc["min_duration"]))
                elif info.audio_tracks:
                    sil = silence.detect_silences_audio(
                        str(src), mic_track(preset, info), float(sc["threshold_db"]),
                        float(sc["min_duration"]), info.duration)
                else:
                    sil = []
                segs = silence.keep_segments(info.duration, sil, float(sc.get("padding", 0.15)))
            if not segs or silence.total(segs) >= info.duration - 0.05:
                segs = None
        out_dur = silence.total(segs) if segs else info.duration
        words_out = silence.remap_words(words, segs) if segs else words

        # ---- auto-zoom
        ztimes: list[float] = []
        if toggles.get("auto_zoom") and words:
            zc = preset["zoom"]
            with timer.step("4b. Auto-zoom (palabras/gritos)"):
                shouts: set[int] = set()
                if zc.get("on_shouts", True) and info.audio_tracks:
                    wav = media.extract_audio(src, work / "mic.wav", mic_track(preset, info))
                    shouts = shout_indices(word_loudness_db(wav, words), float(zc.get("shout_db", 8)))
                ztimes = zoom_times(words, shouts, zc)
                if segs:
                    ztimes = [silence.remap_time(t, segs) for t in ztimes]
        zexpr = zoom_expr(ztimes, float(preset["zoom"].get("amount", 1.15))) if ztimes else None

        if short:
            layout = compute_short_layout(info.width, info.height, preset["layout"])
            out_w, out_h = OUT_W, OUT_H
            hook = _hook_cfg(preset, hook_text, layout.hook_y)
        else:
            layout = None
            out_w, out_h = build_long_filter(info.width, info.height, preset["render"].get("long_resolution", "original"))[1]
            hook = None

        # ---- efectos de sonido
        sfx_events = []
        if toggles.get("sfx"):
            cuts = silence.cut_points(segs) if segs else []
            sfx_events = sfx.build_events(preset["sfx"], ztimes, cuts, hook is not None)

        report(0.59, "Generando subtítulos…")
        files: list[Path] = []
        watermark = _watermark_cfg(preset)
        with timer.step("5. Subtítulos (.ass/.srt)"):
            ass_out = output_dir / f"{stem}.ass"
            ass = subtitles.build_ass(
                words_out, sub_cfg, out_w, out_h, short=short,
                highlight=toggles.get("highlight_keywords", False),
                hook=hook, duration=out_dur,
                show_subtitles=toggles.get("subtitles", True),
                watermark=watermark,
            )
            subtitles.write_text(ass_out, ass)
            files.append(ass_out)
            if toggles.get("export_srt", True):
                srt_out = output_dir / f"{stem}.srt"
                subtitles.write_text(srt_out, subtitles.build_srt(words_out))
                files.append(srt_out)
            words_json = output_dir / f"{stem}_words.json"
            words_json.write_text(
                json.dumps(words_to_json(words_out), ensure_ascii=False, indent=1), encoding="utf-8"
            )
            files.append(words_json)
        burn = toggles.get("subtitles", True) or hook is not None or watermark is not None

        opts = FilterOptions(
            select_expr=silence.select_expr(segs) if segs else None,
            zoom_expr=zexpr,
            fps=info.fps_str,
            progress_bar=preset["progress_bar"] if toggles.get("progress_bar") else None,
            duration=out_dur,
        )
        report(_W_RENDER[0], "Renderizando…")
        out_video = output_dir / f"{stem}_{'short' if short else 'largo'}.mp4"
        engine = preset["render"].get("engine", "ffmpeg")
        if engine == "remotion" and not short:
            notes.append("Remotion solo se usa en el Modo Short; el Modo Largo se renderiza con FFmpeg.")
            engine = "ffmpeg"
        if engine == "remotion":
            from . import remotion_render as rr

            ok, why = rr.status()
            if not ok:
                notes.append(f"Remotion no disponible ({why}); se ha usado FFmpeg.")
                engine = "ffmpeg"
        lo, hi = _W_RENDER
        if engine == "remotion":
            with timer.step("6a. Vídeo base (FFmpeg)"):
                base = work / "base.mp4"
                media.run(rr.build_base_command(
                    info, base, str(preset["audio"]["output_track"]), opts.select_expr, sfx_events,
                    float(preset["sfx"].get("volume", 0.7)), preset["render"].get("encoder", "auto"), work,
                ))
                base_info = media.probe(base)
            with timer.step("6b. Render (Remotion)"):
                props = rr.build_props(
                    base_info, layout, preset["layout"], out_dur, words_out, sub_cfg,
                    show_subtitles=toggles.get("subtitles", True),
                    highlight=toggles.get("highlight_keywords", False),
                    hook=hook, watermark=watermark, progress_bar=opts.progress_bar,
                    zoom_times=ztimes, zoom_amount=float(preset["zoom"].get("amount", 1.15)),
                    fonts=rr.font_entries(work),
                )
                rr.render(
                    props, work, out_video, output_dir / f"{stem}_remotion.log",
                    crf=int(preset["render"].get("crf", 20)) - 2,
                    progress=lambda f: report(lo + (hi - lo) * f, f"Renderizando con Remotion… {f * 100:.0f} %"),
                )
            encoder = "Remotion (h264)"
        else:
            with timer.step("6. Render (FFmpeg)"):
                if short:
                    fg = build_short_filter(layout, preset["layout"], work, ass_path=ass_out if burn else None,
                                            fonts_dir=FONTS_DIR, opts=opts)
                else:
                    fg, _ = build_long_filter(info.width, info.height, preset["render"].get("long_resolution", "original"),
                                              ass_path=ass_out if burn else None, fonts_dir=FONTS_DIR, opts=opts)
                cmd = build_command(
                    info, fg, out_video, preset["render"], str(preset["audio"]["output_track"]),
                    select_expr=opts.select_expr, sfx_events=sfx_events,
                    sfx_volume=float(preset["sfx"].get("volume", 0.7)), script_dir=work,
                )
                run_ffmpeg(
                    cmd, out_dur, output_dir / f"{stem}_ffmpeg.log",
                    lambda f: report(lo + (hi - lo) * f, f"Renderizando… {f * 100:.0f} %"),
                )
            encoder = next((e for e in ("h264_nvenc", "h264_amf") if e in cmd), "libx264")

        n_hl = sum(1 for w in words_out if w.hl)
        mode_txt = f"Short | Layout: {preset['layout']['type']}" if short else "Largo"
        cut_txt = (f"{len(segs) - 1} cortes (−{info.duration - out_dur:.1f} s)" if segs else "—")
        metadata = metadata_text.strip() if metadata_text is not None else llm.format_metadata(llm_result)
        stats = {"duracion_original": round(info.duration, 2), "duracion_final": round(out_dur, 2),
                 "palabras": len(words_out), "cortes": len(segs) - 1 if segs else 0,
                 "zooms": len(ztimes), "sfx": len(sfx_events)}
        summary = (
            f"ClipForge — {src.name}\n"
            f"Preset: {preset.get('name')}  |  Modo: {mode_txt}  |  Estilo: {sub_cfg.get('style')}\n"
            f"Origen: {info.width}x{info.height} @ {float(info.fps):.2f} fps, {info.duration:.1f} s, "
            f"{len(info.audio_tracks)} pista(s) de audio\n"
            f"Salida: {out_w}x{out_h}, {out_dur:.1f} s, {encoder}\n"
            f"Palabras: {len(words_out)}  |  Clave: {n_hl}  |  Gancho: {hook['text'] if hook else '—'}"
            f"{'  |  revisado a mano' if reviewed else ''}\n"
            f"Silencios: {cut_txt}  |  Zooms: {len(ztimes)}  |  Efectos de sonido: {len(sfx_events)}\n"
            + ("".join(f"Aviso: {n}\n" for n in notes))
            + f"\nTiempos:\n{timer.summary()}\n"
            + (f"\n— Textos para publicar (LLM) —\n{metadata}\n" if metadata else "")
        )
        meta = output_dir / f"{stem}_meta.txt"
        meta.write_text(summary, encoding="utf-8")
        files = [out_video, *files, meta]
        report(1.0, "¡Listo!")
        return JobResult(out_video, files, timer.as_dict(), summary, metadata, stats)
    finally:
        shutil.rmtree(work, ignore_errors=True)


_SAMPLE = "qué locura de partida chicos"


def render_preview_frame(
    src: str | Path, preset: dict, t: float, out_png: Path, hook_text: str | None = None
) -> Path:
    """Renderiza un único fotograma con el layout, un subtítulo de ejemplo, el gancho y la marca."""
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
            watermark=_watermark_cfg(preset),
        ))
        if short and preset["render"].get("engine") == "remotion":
            from . import remotion_render as rr

            if rr.status()[0]:
                return _remotion_preview(src, info, preset, layout, sample, hook, t, out_png, work)
        opts = FilterOptions(
            fps=info.fps_str, duration=max(info.duration, 0.1),  # en la vista previa se ve el fondo de la barra
            progress_bar=preset["progress_bar"] if preset["toggles"].get("progress_bar") else None,
        )
        t = max(0.0, min(t, max(info.duration - 0.2, 0.0)))
        if short:
            fg = build_short_filter(layout, preset["layout"], work, ass_path=ass, fonts_dir=FONTS_DIR, opts=opts)
        else:
            fg, _ = build_long_filter(info.width, info.height, preset["render"].get("long_resolution", "original"),
                                      ass_path=ass, fonts_dir=FONTS_DIR, opts=opts)
        cmd = build_command(info, fg, out_png, preset["render"], seek=t, frames=1)
        media.run(cmd)
        return out_png
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _remotion_preview(src, info, preset, layout, sample, hook, t, out_png, work) -> Path:
    """Vista previa con Remotion: un trozo de 1 s del original y un fotograma ya animado."""
    from . import remotion_render as rr

    t = max(0.0, min(t, max(info.duration - 1.0, 0.0)))
    base = work / "base.mp4"
    media.run([
        media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(src),
        "-t", "1", "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", str(base),
    ])
    base_info = media.probe(base)
    props = rr.build_props(
        base_info, layout, preset["layout"], 1.0, sample, preset["subtitles"],
        show_subtitles=preset["toggles"].get("subtitles", True),
        highlight=preset["toggles"].get("highlight_keywords", False),
        hook=hook, watermark=_watermark_cfg(preset),
        progress_bar=preset["progress_bar"] if preset["toggles"].get("progress_bar") else None,
        zoom_times=[], zoom_amount=1.0, fonts=rr.font_entries(work),
    )
    frame = min(int(float(base_info.fps) * 0.6), props["durationInFrames"] - 1)  # animaciones ya asentadas
    return rr.still(props, work, out_png, frame)
