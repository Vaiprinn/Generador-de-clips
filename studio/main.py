"""ClipForge Studio: abre la app en su propia ventana (WebView2 de Windows) con el servidor local detrás.

    .venv\\Scripts\\pythonw.exe -m studio.main        (o el acceso directo «ClipForge Studio»)
    .venv\\Scripts\\python.exe  -m studio.main --browser   (en el navegador, para depurar)
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

LOG = ROOT / "work" / "studio.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
_handlers: list[logging.Handler] = [logging.FileHandler(LOG, encoding="utf-8")]
if sys.stderr is not None:  # con pythonw.exe no hay consola
    _handlers.append(logging.StreamHandler())
else:
    sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8")  # print() y librerías no deben romper
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=_handlers)

import uvicorn  # noqa: E402

from studio import server  # noqa: E402

VIDEO_TYPES = ("Vídeos (*.mp4;*.mkv;*.mov;*.flv;*.ts;*.webm;*.avi;*.m4v)", "Todos los archivos (*.*)")
CHAT_TYPES = ("Chat (*.json;*.jsonl;*.txt;*.csv)", "Todos los archivos (*.*)")


def free_port(preferred: int = 7870) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("No hay puertos libres.")


class Api:
    """Funciones de Python que la página llama con window.pywebview.api.*"""

    def __init__(self):
        self._window = None  # privado: pywebview no expone a la página lo que empieza por _

    def _dialog(self, types, title):
        import webview

        start = str(Path.home() / "Videos")
        res = self._window.create_file_dialog(webview.FileDialog.OPEN, directory=start, allow_multiple=False,
                                             file_types=types)
        return res[0] if res else None

    def pick_video(self):
        return self._dialog(VIDEO_TYPES, "Elige un vídeo")

    def pick_chat(self):
        return self._dialog(CHAT_TYPES, "Elige el chat")

    def save_video(self, path):
        """«Guardar como…» de un vídeo ya hecho; al terminar lo enseña seleccionado en el Explorador."""
        import shutil
        import subprocess

        import webview

        try:
            src = server._output_path(path)
        except server.HTTPException as e:
            return {"error": e.detail}
        if not src.is_file():
            return {"error": "No encuentro ese vídeo."}
        res = self._window.create_file_dialog(webview.FileDialog.SAVE, directory=str(Path.home() / "Videos"),
                                              save_filename=src.name, file_types=("Vídeo MP4 (*.mp4)",))
        if not res:
            return {"cancelled": True}
        dest = Path(res if isinstance(res, str) else res[0])
        if dest.suffix.lower() != src.suffix.lower():
            dest = dest.with_name(dest.name + src.suffix)
        try:
            if dest.resolve() != src.resolve():
                shutil.copy2(src, dest)
        except OSError as e:
            return {"error": f"No se pudo guardar: {e.strerror or e}"}
        subprocess.Popen(["explorer", f"/select,{dest}"])  # noqa: S603, S607
        return {"saved": str(dest)}


def serve(port: int) -> uvicorn.Server:
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    return srv


def main() -> None:
    port = free_port(int(os.environ.get("CLIPFORGE_STUDIO_PORT", "7870")))
    srv = serve(port)
    if server.get_prefs().get("phone"):  # el acceso desde el móvil se queda activado entre sesiones
        err = server.start_lan()
        if err:
            logging.warning(err)
    url = f"http://127.0.0.1:{port}/"
    if "--serve" in sys.argv:  # solo el servidor (pruebas)
        print(f"ClipForge Studio en {url}", flush=True)
        while True:
            time.sleep(1)
    if "--browser" in sys.argv:
        import webbrowser

        print(f"ClipForge Studio en {url}")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return
    import webview

    api = Api()
    window = webview.create_window("ClipForge Studio", url, js_api=api, width=1360, height=880,
                                   min_size=(980, 680), background_color="#0E0F11")
    api._window = window
    # Modo privado: sin carpeta de perfil que otra ventana (o un cierre brusco) pueda dejar bloqueada.
    # Las preferencias se guardan en el servidor (work/studio_prefs.json).
    webview.start(private_mode=True, icon=str(ROOT / "bin" / "clipforge.ico"))
    srv.should_exit = True


if __name__ == "__main__":
    main()
