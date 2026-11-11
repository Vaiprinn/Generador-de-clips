"""ClipForge Studio — servidor local (FastAPI) que usa el motor de /pipeline.

Solo escucha en 127.0.0.1 y cada petición lleva un token aleatorio de la sesión, así que ninguna
otra web abierta en el PC puede usar la API. Los trabajos largos (buscar momentos, renderizar…)
corren en hilos y la interfaz pregunta su progreso cada segundo.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import traceback
import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from pipeline import edits
from pipeline import episode as ep
from pipeline import highlights as hl
from pipeline import media, sfx
from pipeline.job import process_video, render_preview_frame, safe_stem
from pipeline.paths import CACHE_DIR, OUTPUT_DIR, WORK_DIR, ensure_dirs
from pipeline.presets import deep_merge, list_presets, load_preset, save_preset
from pipeline.transcribe import words_from_json

log = logging.getLogger("studio")

WEB = Path(__file__).resolve().parent / "web"
TOKEN = secrets.token_urlsafe(24)
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".ts"}
LONG_VIDEO_S = 10 * 60

PRESET_LABELS = {"gta": "GTA", "eafc27": "EA FC 27", "thefinals": "The Finals", "minecraft": "Minecraft",
                 "largo_youtube": "YouTube largo"}
PRESET_ICONS = {"gta": "🚗", "eafc27": "⚽", "thefinals": "🔫", "minecraft": "⛏️", "largo_youtube": "📺"}

# Ajustes que Studio deja personalizar (y guarda en el preset del juego).
STYLE_KEYS = [
    "handle", "layout.type", "subtitles.style", "subtitles.font_size", "subtitles.primary_color",
    "subtitles.highlight_color", "subtitles.case", "hook.duration", "hook.fill", "hook.outline_color",
    "toggles.subtitles", "toggles.hook_title", "toggles.highlight_keywords", "toggles.cut_silences",
    "toggles.auto_zoom", "toggles.sfx", "toggles.progress_bar", "toggles.watermark", "progress_bar.color",
    "transcription.dialect", "transcription.glossary", "audio.transcribe_track", "llm.provider",
    # encuadre: mover/acercar el juego, seguimiento, HUD de EA FC y tamaño de la cámara de arriba
    "layout.gameplay_zoom", "layout.gameplay_offset", "layout.gameplay_offset_y", "layout.track",
    "layout.hud_score", "layout.hud_radar", "layout.top_cam_width",
    # cómo arranca el Short: animación del gancho, su sonido y el efecto de entrada del vídeo
    "hook.anim", "hook.sound", "intro.effect", "hook.fade_out", "hook.sound_dur",
]
FX_KEYS = ("hook.anim", "hook.sound", "intro.effect", "hook.fade_out", "hook.sound_dur")
FACECAM_LAYOUTS = ("facecam_esquina", "split", "blur_cam", "seguimiento")

app = FastAPI(title="ClipForge Studio", docs_url=None, redoc_url=None)

# ---------------------------------------------------------------- seguridad y utilidades


def _phone_token() -> str:
    """Token fijo del móvil (se guarda para que el acceso de la pantalla de inicio siga valiendo)."""
    f = WORK_DIR / "phone_token.txt"
    try:
        t = f.read_text(encoding="utf-8").strip()
        if len(t) >= 20:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(18)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(t, encoding="utf-8")
    return t


PHONE_TOKEN = _phone_token()
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _is_remote(request: Request) -> bool:
    return (request.client.host if request.client else "") not in LOCAL_HOSTS


@app.middleware("http")
async def require_token(request: Request, call_next):
    if _is_remote(request):
        # Desde el móvil (otra IP de la Wi-Fi) TODO pide el token del móvil: página, estáticos y API.
        given = (request.headers.get("x-token") or request.query_params.get("k") or request.cookies.get("cf") or "")
        if not secrets.compare_digest(given, PHONE_TOKEN):
            return JSONResponse({"detail": "Abre ClipForge desde el código QR de tu PC."}, status_code=403)
        resp = await call_next(request)
        if request.query_params.get("k") == PHONE_TOKEN and not request.url.path.startswith("/api/"):
            resp.set_cookie("cf", PHONE_TOKEN, max_age=60 * 60 * 24 * 365, httponly=True, samesite="strict")
        return resp
    if request.url.path.startswith("/api/"):
        if request.headers.get("x-token") != TOKEN and request.query_params.get("k") != TOKEN:
            return JSONResponse({"detail": "token"}, status_code=403)
    return await call_next(request)


def _get(d: dict, dotted: str):
    for k in dotted.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _set(d: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _video_path(path: str) -> Path:
    p = Path((path or "").strip().strip('"'))
    if not p.is_file() or p.suffix.lower() not in VIDEO_EXT:
        raise HTTPException(400, f"No es un vídeo válido: {path}")
    return p


def _output_path(path: str) -> Path:
    p = Path(path).resolve()
    if OUTPUT_DIR.resolve() not in p.parents and p != OUTPUT_DIR.resolve():
        raise HTTPException(400, "Fuera de la carpeta de salida.")
    return p


def build_preset(name: str, style: dict | None = None) -> dict:
    preset = load_preset(name or "gta")
    for k, v in (style or {}).items():
        if k in STYLE_KEYS and v is not None and v != {}:
            _set(preset, k, v)
    if preset.get("handle"):
        preset["handle"] = preset["handle"] if preset["handle"].startswith("@") else "@" + preset["handle"]
    return preset


def resolve_framing(src: Path, preset: dict, framing: str = "auto") -> tuple[dict, str]:
    """Encuadre del Short. «auto»: busca la webcam; si no hay, usa Blur (el juego entero)."""
    preset = copy.deepcopy(preset)
    lay = preset["layout"]
    if framing and framing != "auto":
        lay["type"] = framing
    if lay["type"] in FACECAM_LAYOUTS or framing == "auto":
        rect, inner = _facecam_cached(src)
        if rect:
            lay["facecam_rect"] = rect
            lay["facecam_inner"] = inner or rect
            if framing == "auto" and lay["type"] not in FACECAM_LAYOUTS:
                lay["type"] = "facecam_esquina"
            return preset, f"Webcam encontrada · {LAYOUT_NAMES.get(lay['type'], lay['type'])}."
        if lay["type"] == "seguimiento":
            lay["facecam_rect"] = []
            return preset, "No se ve ninguna webcam: seguimiento del juego sin cámara."
        if lay["type"] in FACECAM_LAYOUTS:
            lay["type"] = "blur"
            return preset, "No se ve ninguna webcam: se usa Blur (el juego entero con fondo desenfocado)."
    if lay["type"] == "sin_facecam":  # sin cámara en el Short, pero la del vídeo se tapa en el juego
        rect, _ = _facecam_cached(src)
        if rect:
            lay["hide_rect"] = rect
    return preset, LAYOUT_NAMES.get(lay["type"], lay["type"]) + "."


LAYOUT_NAMES = {"facecam_esquina": "Cámara en la esquina", "split": "Split", "blur": "Blur",
                "sin_facecam": "Solo juego", "blur_cam": "Blur con cámara arriba", "seguimiento": "Seguimiento (EA FC)"}
_FACECAM_CACHE: dict[str, tuple] = {}
FACECAM_VERSION = 2  # súbelo si cambia el detector: invalida lo guardado en disco
_FACECAM_LOCK = threading.Lock()  # la vista previa pide geometría y seguimiento a la vez: una búsqueda


def _facecam_cached(src: Path) -> tuple:
    """(recuadro de la webcam, parte con imagen) o (None, None). Se guarda también en disco: la
    búsqueda tarda 5-10 s y, mientras, la vista previa no puede dibujarse."""
    key = f"{src.resolve()}|{src.stat().st_mtime}|{FACECAM_VERSION}"
    with _FACECAM_LOCK:
        return _facecam_find(src, key)


def _facecam_find(src: Path, key: str) -> tuple:
    if key in _FACECAM_CACHE:
        return _FACECAM_CACHE[key]
    disk = CACHE_DIR / f"facecam_{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"
    try:
        _FACECAM_CACHE[key] = tuple(json.loads(disk.read_text(encoding="utf-8")))
        return _FACECAM_CACHE[key]
    except (OSError, ValueError):
        pass
    try:
        from pipeline.facedetect import detect_facecam

        found = detect_facecam(src, inner=True)
        _FACECAM_CACHE[key] = found
        disk.write_text(json.dumps(list(found)), encoding="utf-8")
    except Exception as e:
        log.warning("facecam: %s", e)
        _FACECAM_CACHE[key] = (None, None)
    return _FACECAM_CACHE[key]


# ---------------------------------------------------------------- trabajos en segundo plano

JOBS: dict[str, dict] = {}
_RUN_LOCK = threading.Lock()  # un trabajo pesado cada vez: la CPU no da para dos Whisper a la vez


def start_job(kind: str, fn, *args) -> str:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "status": "en cola", "progress": 0.0, "message": "En cola…",
           "result": None, "error": None, "started": time.time()}
    JOBS[jid] = job

    def report(frac: float, msg: str) -> None:
        job["progress"] = max(0.0, min(1.0, float(frac)))
        job["message"] = msg

    def run():
        with _RUN_LOCK:
            job["status"] = "trabajando"
            try:
                job["result"] = fn(report, *args)
                job["status"] = "listo"
                job["progress"] = 1.0
            except Exception as e:
                log.exception("Trabajo %s", kind)
                job["status"] = "error"
                job["error"] = str(e) or e.__class__.__name__
                job["trace"] = traceback.format_exc(limit=3)
            job["elapsed"] = round(time.time() - job["started"], 1)

    threading.Thread(target=run, daemon=True).start()
    return jid


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, "No existe ese trabajo.")
    return {k: v for k, v in job.items() if k != "trace"}


# ---------------------------------------------------------------- página


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    html = (WEB / "index.html").read_text(encoding="utf-8")
    remote = _is_remote(request)
    html = html.replace("__TOKEN__", PHONE_TOKEN if remote else TOKEN)
    return html.replace("__PHONE__", "true" if remote else "false")


@app.get("/manifest.webmanifest")
def manifest():
    """Para «Añadir a pantalla de inicio» en el iPhone: se abre a pantalla completa como una app."""
    return JSONResponse({
        "name": "ClipForge Studio", "short_name": "ClipForge", "display": "standalone",
        "start_url": f"/?k={PHONE_TOKEN}", "background_color": "#0E0F11", "theme_color": "#0E0F11",
        "icons": [{"src": f"/static/icon-512.png?k={PHONE_TOKEN}", "sizes": "512x512", "type": "image/png"}],
    }, media_type="application/manifest+json")


# ---------------------------------------------------------------- móvil (misma Wi-Fi)

PHONE_PORT = int(os.environ.get("CLIPFORGE_PHONE_PORT", "7880"))
_LAN: dict = {"server": None, "error": ""}


def lan_ip() -> str:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
        try:
            sk.connect(("192.168.1.1", 9))  # no envía nada: solo elige la interfaz de la red local
            return sk.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def start_lan() -> str:
    """Abre Studio a la red local (puerto fijo) para el móvil. Devuelve un error o ''."""
    if _LAN["server"] is not None:
        return ""
    import uvicorn

    srv = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=PHONE_PORT, log_level="warning"))  # noqa: S104
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(60):
        if srv.started:
            _LAN["server"], _LAN["error"] = srv, ""
            return ""
        time.sleep(0.05)
    _LAN["error"] = f"No se pudo abrir el puerto {PHONE_PORT} (¿otra copia de Studio abierta?)."
    return _LAN["error"]


def stop_lan() -> None:
    if _LAN["server"] is not None:
        _LAN["server"].should_exit = True
        _LAN["server"] = None


def _phone_info() -> dict:
    import segno

    url = f"http://{lan_ip()}:{PHONE_PORT}/?k={PHONE_TOKEN}"
    on = _LAN["server"] is not None
    qr = segno.make(url, error="m").svg_inline(scale=6, dark="#0E0F11", light="#FFFFFF") if on else ""
    return {"on": on, "url": url if on else "", "qr": qr, "error": _LAN["error"]}


@app.get("/api/phone")
def phone_get(request: Request):
    if _is_remote(request):
        return {"on": True, "url": "", "qr": "", "error": ""}
    return _phone_info()


@app.post("/api/phone")
def phone_set(request: Request, body: dict = Body(...)):
    if _is_remote(request):
        raise HTTPException(403, "Esto se cambia desde el PC.")
    on = bool(body.get("on"))
    if on:
        start_lan()
    else:
        stop_lan()
    prefs = {**get_prefs(), "phone": on and _LAN["server"] is not None}
    PREFS.write_text(json.dumps(prefs), encoding="utf-8")
    return _phone_info()


UPLOAD_DIR = WORK_DIR.parent / "input" / "movil"


@app.put("/api/upload")
async def upload(request: Request, name: str):
    """Sube un vídeo desde el móvil (cuerpo = el archivo tal cual) a input/movil."""
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(400, "Eso no es un vídeo.")
    safe = "".join(c if c.isalnum() or c in " -_()" else "_" for c in stem).strip()[:60] or "video"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dst = UPLOAD_DIR / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{safe}{ext}"
    tmp = dst.with_suffix(dst.suffix + ".part")
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, "El archivo llegó vacío.")
        tmp.replace(dst)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"path": str(dst), "size": size}


def _video_roots() -> list[Path]:
    home = Path.home()
    roots = [UPLOAD_DIR, WORK_DIR.parent / "input", home / "Videos", home / "Desktop", home / "Downloads",
             home / "OneDrive" / "Escritorio", home / "OneDrive" / "Desktop"]
    return [r for r in roots if r.is_dir()]


@app.get("/api/browse")
def browse():
    """Vídeos recientes del PC (para elegir desde el móvil sin escribir rutas)."""
    found: dict[str, dict] = {}
    for root in _video_roots():
        for depth, pattern in ((0, "*"), (1, "*/*"), (2, "*/*/*")):
            for f in root.glob(pattern):
                if f.suffix.lower() in VIDEO_EXT and f.is_file() and "_short" not in f.stem:
                    st = f.stat()
                    found[str(f)] = {"path": str(f), "name": f.name, "folder": f.parent.name,
                                     "size": st.st_size, "mtime": st.st_mtime}
    items = sorted(found.values(), key=lambda x: x["mtime"], reverse=True)[:60]
    return {"videos": items}


@app.get("/static/{name}")
def static(name: str):
    p = (WEB / name).resolve()
    if p.parent != WEB.resolve() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- datos


@app.get("/api/presets")
def presets():
    out = []
    for n in list_presets() or ["default"]:
        p = load_preset(n)
        out.append({"id": n, "label": PRESET_LABELS.get(n, n), "icon": PRESET_ICONS.get(n, "🎮"),
                    "mode": p.get("mode", "short"), "style": {k: _get(p, k) for k in STYLE_KEYS}})
    return {"presets": out, "ai_keys": {"claude": bool(os.environ.get("ANTHROPIC_API_KEY")),
                                        "openai": bool(os.environ.get("OPENAI_API_KEY"))}}


@app.post("/api/presets/{name}")
def save_style(name: str, style: dict = Body(...)):
    preset = build_preset(name, style)
    save_preset(preset, name)
    return {"ok": True}


PREFS = WORK_DIR / "studio_prefs.json"


@app.get("/api/prefs")
def get_prefs():
    try:
        return json.loads(PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@app.post("/api/prefs")
def set_prefs(body: dict = Body(...)):
    prefs = {**get_prefs(), **{k: v for k, v in body.items() if k in ("preset", "ai", "framing")}}
    PREFS.parent.mkdir(parents=True, exist_ok=True)
    PREFS.write_text(json.dumps(prefs), encoding="utf-8")
    return prefs


@app.post("/api/probe")
def probe(body: dict = Body(...)):
    src = _video_path(body.get("path", ""))
    info = media.probe(src)
    return {
        "path": str(src), "name": src.name, "size_mb": round(src.stat().st_size / 1048576),
        "duration": info.duration, "width": info.width, "height": info.height, "fps": float(info.fps),
        "audio_tracks": len(info.audio_tracks),
        "suggest": "momentos" if info.duration >= LONG_VIDEO_S else "short",
        "project": _project_summary(hl.vod_out_dir(src)),
    }


@app.get("/api/frame")
def frame(path: str, t: float = 0.0, w: int = 640):
    src = _video_path(path)
    key = hashlib.sha1(f"{src.resolve()}|{src.stat().st_mtime}|{t:.2f}|{w}".encode()).hexdigest()[:16]
    out = CACHE_DIR / f"frame_{key}.jpg"
    if not out.exists():
        ensure_dirs()
        from pipeline.paths import ffmpeg_bin

        media.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(t, 0):.2f}", "-i", str(src),
                   "-frames:v", "1", "-vf", f"scale={w}:-2", "-q:v", "4", str(out)])
    return FileResponse(out, headers={"Cache-Control": "max-age=3600"})


@app.get("/api/media")
def media_file(path: str, dl: int = 0):
    """Vídeo para los reproductores (con Range: se puede saltar a cualquier punto de un VOD de 8 h).
    `dl=1` lo descarga con su nombre (para guardarlo en el móvil)."""
    p = Path(path)
    if p.suffix.lower() in VIDEO_EXT and p.is_file():
        return FileResponse(p, filename=p.name if dl else None)
    if p.suffix.lower() in {".png", ".jpg"} and p.is_file():
        return FileResponse(_output_path(path))
    raise HTTPException(404)


@app.post("/api/preview")
def preview(body: dict = Body(...)):
    """Un fotograma del Short tal cual saldrá (layout, subtítulo de ejemplo, gancho, marca)."""
    src = _video_path(body.get("path", ""))
    preset = build_preset(body.get("preset"), body.get("style"))
    preset["mode"] = "short"
    preset, note = resolve_framing(src, preset, body.get("framing", "auto"))
    t = float(body.get("t") or 0)
    key = hashlib.sha1(json.dumps([str(src), t, preset, body.get("hook")], sort_keys=True, default=str).encode()).hexdigest()[:16]
    out = CACHE_DIR / f"preview_{key}.png"
    if not out.exists():
        render_preview_frame(src, preset, t, out, body.get("hook") or "TU GANCHO AQUÍ")
    return {"url": f"/api/png?name={out.name}", "note": note}


def _rect(r):
    return None if r is None else [r.x, r.y, r.w, r.h]


@app.post("/api/layout")
def layout_geometry(body: dict = Body(...)):
    """Geometría del Short para la vista previa en vivo (la dibuja la página con el vídeo original)."""
    from pipeline.job import _hook_cfg
    from pipeline.layout import compute_short_layout

    src = _video_path(body.get("path", ""))
    preset = build_preset(body.get("preset"), body.get("style"))
    preset["mode"] = "short"
    preset, note = resolve_framing(src, preset, body.get("framing", "auto"))
    info = media.probe(src)
    lay = compute_short_layout(info.width, info.height, preset["layout"])
    preset["toggles"]["hook_title"] = True
    hook = _hook_cfg(preset, body.get("hook") or "", lay.hook_y) if body.get("hook") else None
    subs = preset["subtitles"]
    return {
        "note": note, "type": lay.type, "src": [info.width, info.height],
        "gameplay": _rect(lay.gameplay), "gameplay_dst": _rect(lay.gameplay_dst),
        "facecam_src": _rect(lay.facecam_src), "facecam_dst": _rect(lay.facecam_dst),
        "background": _rect(lay.background), "fit": lay.fit,
        "hud": [[_rect(a), _rect(b)] for a, b in lay.hud], "hide": [_rect(r) for r in lay.hide],
        "radius": int(preset["layout"].get("facecam_radius", 28)), "track": lay.track,
        "track_mode": preset["layout"].get("track", "no"),
        "subs": {"y": lay.subs_y or subs.get("pos_y", 1100), "size": subs.get("font_size", 72),
                 "color": subs.get("primary_color", "#FFFFFF"), "hl": subs.get("highlight_color", "#FFE14D"),
                 "case": subs.get("case", "original"), "on": bool(preset["toggles"].get("subtitles", True))},
        "hook": hook and {"text": hook["text"], "y": hook.get("pos_y") or lay.hook_y, "size": hook.get("font_size", 120),
                          "fill": hook.get("fill", "#FFFFFF"), "outline": hook.get("outline_color", "#E3262E"),
                          "duration": hook.get("duration", 2.5), "fade": float(hook.get("fade_out", 0.6) or 0)},
        "handle": preset.get("handle") if preset["toggles"].get("watermark") else "",
    }


@app.post("/api/track")
def track(body: dict = Body(...)):
    """Recorrido de la cámara virtual (seguimiento) entre start y end, para la vista previa."""
    from pipeline import tracking
    from pipeline.job import track_exclusions
    from pipeline.layout import compute_short_layout

    src = _video_path(body.get("path", ""))
    preset = build_preset(body.get("preset"), body.get("style"))
    preset, _ = resolve_framing(src, preset, body.get("framing", "auto"))
    info = media.probe(src)
    lay = compute_short_layout(info.width, info.height, preset["layout"])
    start = max(0.0, float(body.get("start") or 0))
    end = min(info.duration, float(body.get("end") or info.duration), start + 180)
    mode = preset["layout"].get("track", "movimiento")
    if mode == "no":
        mode = "movimiento"
    key = hashlib.sha1(json.dumps([str(src), src.stat().st_mtime, start, end, mode, preset["layout"].get("facecam_rect"),
                                   lay.gameplay.w]).encode()).hexdigest()[:16]
    cache = CACHE_DIR / f"track_{key}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    raw = tracking.track_path(src, start, end - start, info.width, info.height,
                              track_exclusions(preset["layout"]), mode)
    smooth = tracking.smooth_path(raw, lay.gameplay.w / info.width)
    out = {"start": start, "points": [[round(t + start, 3), round(x, 4)] for t, x in smooth],
           "crop_w": lay.gameplay.w, "src_w": info.width}
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


@app.get("/api/font/{name}")
def font(name: str):
    from pipeline.paths import FONTS_DIR

    p = (FONTS_DIR / name).resolve()
    if p.parent != FONTS_DIR.resolve() or p.suffix.lower() not in (".ttf", ".otf") or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "max-age=86400"})


@app.get("/api/png")
def png(name: str):
    p = (CACHE_DIR / name).resolve()
    if p.parent != CACHE_DIR.resolve() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


# ---------------------------------------------------------------- proyectos (carpetas en output/)


def _project_summary(d: Path) -> dict | None:
    if not d.is_dir():
        return None
    cand = d / "candidatos.json"
    plan = d / "youtube" / "plan.json"
    clips = sorted((d / "clips").glob("*_short.mp4")) if (d / "clips").is_dir() else []
    vod = None
    if cand.exists():
        vod = json.loads(cand.read_text(encoding="utf-8")).get("vod")
    elif plan.exists():
        vod = json.loads(plan.read_text(encoding="utf-8")).get("src")
    return {"dir": str(d), "name": d.name, "vod": vod, "has_moments": cand.exists(), "has_episode": plan.exists(),
            "episode_done": (d / "youtube" / "episodio.mp4").exists(), "clips": len(clips),
            "updated": max((p.stat().st_mtime for p in d.rglob("*") if p.is_file()), default=d.stat().st_mtime)}


@app.get("/api/projects")
def projects():
    ensure_dirs()
    out = [s for d in OUTPUT_DIR.iterdir() if d.is_dir() and (s := _project_summary(d)) and s["vod"]]
    return {"projects": sorted(out, key=lambda p: p["updated"], reverse=True)[:12]}


@app.post("/api/open")
def open_folder(request: Request, body: dict = Body(...)):
    if _is_remote(request):
        raise HTTPException(400, "La carpeta se abre en el PC: usa ⬇️ para bajar el vídeo al móvil.")
    p = _output_path(body.get("path") or str(OUTPUT_DIR))
    target = p if p.is_dir() else p.parent
    if hasattr(os, "startfile"):
        os.startfile(target)  # noqa: S606 — abre la carpeta en el Explorador
    return {"ok": True}


# ---------------------------------------------------------------- momentos


def _clip_json(c: hl.Clip) -> dict:
    return asdict(c)


@app.post("/api/moments/find")
def moments_find(body: dict = Body(...)):
    src = _video_path(body.get("path", ""))
    preset = build_preset(body.get("preset"), body.get("style"))
    preset["llm"]["provider"] = body.get("ai") or "ninguno"
    preset["highlights"] = {"max_clips": int(body.get("max_clips") or 8),
                            "transcribe": body.get("transcribe") or "auto",
                            "max_len": float(body.get("max_len") or 60)}
    chat = body.get("chat") or None

    def work(report):
        res = hl.find_highlights(src, preset, chat, report)
        return {"dir": str(res.out_dir), "clips": [_clip_json(c) for c in res.clips], "notes": res.notes,
                "timings": res.timings, "duration": res.duration}

    return {"job": start_job("momentos", work)}


@app.get("/api/moments")
def moments_load(dir: str):
    d = _output_path(dir)
    data = json.loads((d / "candidatos.json").read_text(encoding="utf-8"))
    rendered = {p.name: str(p) for p in (d / "clips").glob("*_short.mp4")} if (d / "clips").is_dir() else {}
    return {"dir": str(d), "vod": data["vod"], "duration": data["duracion"], "clips": data["clips"],
            "notes": data.get("avisos", []), "rendered": rendered}


@app.post("/api/moments/render")
def moments_render(body: dict = Body(...)):
    d = _output_path(body["dir"])
    data = json.loads((d / "candidatos.json").read_text(encoding="utf-8"))
    src = _video_path(data["vod"])
    words = words_from_json(json.loads((d / "palabras.json").read_text(encoding="utf-8")))
    clips = [hl.Clip(float(c["start"]), float(c["end"]), float(c.get("score") or 0), c.get("why", ""),
                     c.get("hook", ""), c.get("title", ""), list(c.get("hashtags") or []), True)
             for c in body.get("clips") or [] if c.get("keep", True)]
    if not clips:
        raise HTTPException(400, "No hay ningún clip aprobado.")
    # Guardar las ediciones (inicio/fin/gancho/título/aprobado) para la próxima vez.
    data["clips"] = body.get("clips")
    (d / "candidatos.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    preset = build_preset(body.get("preset"), body.get("style"))
    preset["mode"] = "short"
    preset["toggles"]["hook_title"] = preset["toggles"].get("hook_title", True) or any(c.hook for c in clips)

    def work(report):
        p, note = resolve_framing(src, preset, body.get("framing", "auto"))
        results = hl.render_clips(src, clips, words, p, d, report)
        out = []
        for c, r in results:
            if isinstance(r, Exception):
                out.append({"title": c.title or c.hook, "error": str(r)})
            else:
                # el trozo cortado del VOD se borra: para corregirlo luego se vuelve a cortar
                _remember_request(r.video, body, src, [c.start, c.end])
                out.append({"title": c.title or c.hook or hl.fmt_time(c.start), "video": str(r.video),
                            "meta": str(r.video.with_name(r.video.name.replace("_short.mp4", "_meta.txt"))),
                            "duration": c.duration, "hook": c.hook, "hashtags": c.hashtags})
        return {"clips": out, "folder": str(d / "clips"), "note": note}

    return {"job": start_job("render_momentos", work)}


# ---------------------------------------------------------------- episodio de YouTube


@app.post("/api/episode/plan")
def episode_plan(body: dict = Body(...)):
    src = _video_path(body.get("path", ""))
    preset = build_preset(body.get("preset"), body.get("style"))
    preset["llm"]["provider"] = body.get("ai") or "ninguno"
    over = {"target_min": float(body.get("target_min") or 20), "intro": bool(body.get("intro", True)),
            "cut_silences": bool(body.get("cut_silences", True))}

    def work(report):
        plan, _ = ep.plan_episode(src, preset, body.get("chat") or None, report, overrides=over)
        return {"dir": str(plan.out_dir), "plan": plan.to_dict()}

    return {"job": start_job("episodio", work)}


@app.get("/api/episode")
def episode_load(dir: str):
    d = _output_path(dir)
    plan, _ = ep.load_plan(d)
    done = {k: str(d / f) for k, f in (("video", "episodio.mp4"), ("thumbnail", "miniatura.png"),
                                       ("description", "descripcion.txt"), ("srt", "episodio.srt")) if (d / f).exists()}
    desc = (d / "descripcion.txt").read_text(encoding="utf-8") if (d / "descripcion.txt").exists() else ""
    return {"dir": str(d), "plan": plan.to_dict(), "done": done, "description_text": desc}


@app.post("/api/episode/thumb")
def episode_thumb(body: dict = Body(...)):
    d = _output_path(body["dir"])
    plan, _ = ep.load_plan(d)
    preset = build_preset(body.get("preset"), body.get("style"))
    t = float(body.get("t", plan.thumb_t))
    text = body.get("text", plan.thumb_text)
    key = hashlib.sha1(json.dumps([plan.src, t, text, preset.get("hook"), preset.get("handle")], default=str).encode()).hexdigest()[:16]
    out = CACHE_DIR / f"thumb_{key}.png"
    if not out.exists():
        ep.make_thumbnail(plan.src, t, text, out, preset.get("hook"), (preset.get("handle") or "").strip())
    return {"url": f"/api/png?name={out.name}"}


@app.post("/api/episode/render")
def episode_render(body: dict = Body(...)):
    d = _output_path(body["dir"])
    old, words = ep.load_plan(d)
    plan = ep.EpisodePlan.from_dict({**old.to_dict(), **(body.get("plan") or {}), "src": old.src})
    plan.save(d)
    preset = build_preset(body.get("preset"), body.get("style"))

    def work(report):
        res = ep.render_episode(plan, words, preset, report, d)
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in res.items()} | {
            "description_text": Path(res["description"]).read_text(encoding="utf-8"), "folder": str(d)}

    return {"job": start_job("render_episodio", work)}


# ---------------------------------------------------------------- Short de un clip


@app.post("/api/short/render")
def short_render(body: dict = Body(...)):
    src = _video_path(body.get("path", ""))
    preset = build_preset(body.get("preset"), body.get("style"))
    preset["mode"] = "short"
    hook = (body.get("hook") or "").strip()
    if hook:
        preset["toggles"]["hook_title"] = True
    trim = None
    if body.get("end"):
        trim = (float(body.get("start") or 0), float(body["end"]))
    out_dir = OUTPUT_DIR / "shorts"
    preset["zooms"] = list(body.get("zooms") or [])

    def work(report):
        p, note = resolve_framing(src, preset, body.get("framing", "auto"))
        res = process_video(src, p, progress=report, output_dir=out_dir,
                            stem=f"{safe_stem(src)}_{time.strftime('%H%M%S')}", hook_text=hook or None, trim=trim)
        _remember_request(res.video, body, src, None)
        return {"video": str(res.video), "summary": res.summary, "folder": str(out_dir), "note": note}

    return {"job": start_job("short", work)}


# ---------------------------------------------------------------- corregir un Short ya hecho


def _remember_request(video: Path, body: dict, src: Path, cut: list | None) -> None:
    """Guarda en el registro de edición con qué ajustes se hizo (para re-renderizar igual)."""
    try:
        rec = edits.load(video)
    except (OSError, ValueError):
        return
    rec["source"] = str(src)
    rec["cut"] = cut
    rec["request"] = {"preset": body.get("preset"), "style": body.get("style") or {},
                      "framing": body.get("framing", "auto"), "zooms": body.get("zooms") or []}
    edits.save(rec, video)


@app.get("/api/edit")
def edit_load(video: str):
    v = _output_path(video)
    try:
        rec = edits.load(v)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    style = build_preset((rec.get("request") or {}).get("preset") or "gta", (rec.get("request") or {}).get("style"))
    mark = bool(style["toggles"].get("highlight_keywords")) or style["subtitles"].get("style") == "resaltado"
    return {"video": str(v), "hook": rec.get("hook", ""), "phrases": edits.phrases(rec, mark),
            "fx": {k: (_get(style, k) if k == "hook.fade_out" else _get(style, k) or "") for k in FX_KEYS}}


@app.post("/api/edit/render")
def edit_render(body: dict = Body(...)):
    video = _output_path(body.get("video", ""))
    try:
        rec = edits.load(video)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    if "source" not in rec:
        raise HTTPException(400, "A este Short le faltan datos para rehacerlo: créalo otra vez.")
    req = rec.get("request") or {}
    style = {**(req.get("style") or {}), **{k: v for k, v in (body.get("fx") or {}).items() if k in FX_KEYS}}
    preset = build_preset(req.get("preset") or "gta", style)
    preset["mode"] = "short"
    preset["zooms"] = req.get("zooms") or []
    hook = (body.get("hook") or "").strip()
    preset["toggles"]["hook_title"] = bool(hook)
    words = edits.apply(rec, list(body.get("texts") or []))
    if any(w.hl for w in words):  # marcaste *palabras*: que se vean en color
        style["toggles.highlight_keywords"] = True
        preset["toggles"]["highlight_keywords"] = True
    src = _video_path(rec["source"])
    request = {**req, "style": style}

    def work(report):
        import shutil
        import tempfile

        p, note = resolve_framing(src, preset, req.get("framing", "auto"))
        tmp = Path(tempfile.mkdtemp(prefix="edit_", dir=WORK_DIR))
        try:
            inp = src
            if rec.get("cut"):
                report(0.02, "Cortando el trozo del directo…")
                a, b = rec["cut"]
                inp = hl.cut_clip(src, float(a), float(b), tmp / "clip.mp4", p["render"].get("encoder", "auto"))
            res = process_video(inp, p, progress=report, output_dir=video.parent, stem=edits.next_stem(rec["stem"]),
                                words=words, reviewed=True, hook_text=hook,
                                trim=tuple(rec["trim"]) if rec.get("trim") else None,
                                metadata_text=rec.get("metadata") or None)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        new = edits.load(res.video)
        new.update(source=str(src), cut=rec.get("cut"), request=request)
        edits.save(new, res.video)
        return {"video": str(res.video), "hook": hook, "note": note}

    return {"job": start_job("corregir", work)}


# ---------------------------------------------------------------- sonidos


@app.get("/api/sfx")
def sfx_list():
    return {"sounds": [{"file": f, "label": sfx.LABELS.get(f, Path(f).stem.replace("_", " ")),
                        "dur": sfx.duration(sfx.SFX_DIR / f)} for f in sfx.list_sfx()]}


@app.get("/api/sfx/file/{name}")
def sfx_file(name: str):
    p = sfx.resolve(name)
    if not p:
        raise HTTPException(404, "No existe ese sonido.")
    return FileResponse(p)


@app.put("/api/sfx/upload")
async def sfx_upload(request: Request, name: str):
    """Añade un sonido propio (wav/mp3…) a assets/sfx: sale en la lista con su nombre."""
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    if ext not in sfx.AUDIO_EXT:
        raise HTTPException(400, "Eso no es un audio (wav, mp3, ogg, flac, m4a).")
    data = await request.body()
    if not data or len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "El audio tiene que pesar menos de 15 MB.")
    safe = "".join(c if c.isalnum() or c in " -_()" else "_" for c in stem).strip()[:40] or "sonido"
    sfx.ensure_default_sfx()
    dst = sfx.SFX_DIR / f"{safe}{ext}"
    dst.write_bytes(data)
    return {"file": dst.name}
