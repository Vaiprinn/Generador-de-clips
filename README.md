# 🎮 ClipForge

App **local** para Windows (Python 3.11) que edita y subtitula vídeos de gaming en español.
Todo offline y gratis: faster-whisper + FFmpeg + Gradio en `http://127.0.0.1:7860`.

> **Estado: Fase 2** — Modo Short 1080x1920 (layouts Facecam esquina, Split, Blur y Sin facecam) y
> Modo Largo 16:9 con .srt; estilos Frase, Resaltado y Karaoke; Título gancho; tabla de revisión
> de la transcripción; editor de presets con marcado por clics y autodetección de facecam.

## Arranque rápido (Windows)

1. Instala Python 3.11 (`winget install Python.Python.3.11`).
2. Doble clic en **`run.bat`**. La primera vez:
   - crea `.venv` e instala `requirements.txt` (y `requirements-gpu.txt` si detecta una NVIDIA),
   - descarga `ffmpeg.exe`/`ffprobe.exe` en `bin/` y las fuentes libres en `assets/fonts/`,
   - abre el navegador.
3. El modelo Whisper (`large-v3-turbo`, ~1,6 GB) se descarga en `models/` la primera vez que transcribes.

## Uso

1. Arrastra uno o varios vídeos, elige **preset** (`gta`, `eafc27`, `thefinals`, `minecraft`,
   `largo_youtube`) y **modo** (Short 9:16 o Largo 16:9).
2. **Layout** (solo Short):
   - *Facecam esquina*: gameplay 9:16 a pantalla completa + facecam flotante redondeada con sombra.
   - *Split*: facecam arriba a todo el ancho, gameplay abajo (alto ajustable).
   - *Blur*: gameplay 16:9 centrado sobre el mismo vídeo desenfocado.
   - *Sin facecam*.
   Marca la facecam o el gameplay con **dos clics** (esquinas opuestas) sobre el fotograma original,
   usa **🔎 Autodetectar facecam** (necesita `opencv-python`, ver extras) o ajusta los deslizadores.
   **👁 Vista previa** enseña el original (rojo = facecam, azul = gameplay) y el resultado.
3. **Subtítulos** — al cambiar de estilo se cargan sus valores típicos:
   - *Frase*: 3–5 palabras, blanco con contorno negro y sombra (Nunito Black).
   - *Resaltado*: 2–5 palabras en minúsculas, sombra paralela, palabras clave en amarillo `#FFE14D`.
   - *Karaoke*: 1–3 palabras en MAYÚSCULAS (Montserrat Black); la palabra que se dice cambia de
     color y hace un "pop" al 115 %. Un evento por palabra.
   Solo se ven mientras hablas. En Short nunca entran en los 660 px de abajo ni en la columna derecha.
4. **Título gancho** (interruptor, solo Short): cursiva gruesa, relleno blanco, contorno rojo y
   contorno exterior blanco (sticker). Durante N segundos o todo el clip; por defecto debajo de la
   facecam. Usa `|` para partirlo en dos líneas.
5. **Revisar transcripción** (opcional): *Transcribir para revisar* → corrige palabras y tiempos,
   marca/desmarca qué resaltar (vacía una celda para borrar la palabra) y escribe el gancho de cada
   vídeo. Las palabras clave vienen pre-marcadas (glosario, exclamaciones, gritos, números, nombres).
6. **🚀 Procesar todos**. Los archivos salen en `output/`:

| Archivo | Contenido |
|---|---|
| `<nombre>_short.mp4` / `<nombre>_largo.mp4` | H.264 (NVENC si hay NVIDIA, si no x264 CRF 20), AAC 48 kHz, fps del original |
| `<nombre>.ass` | Subtítulos quemados (editables con Aegisub) |
| `<nombre>.srt` | Subtítulos aparte, en líneas más largas y con el texto original (para YouTube) |
| `<nombre>_words.json` | Palabras con tiempos, probabilidad y marca de resaltado |
| `<nombre>_meta.txt` | Resumen y **tiempo de cada paso** |
| `<nombre>_ffmpeg.log` | Comando y log de FFmpeg (para depurar) |

**Modo Largo**: mantiene el 16:9 (resolución original o 1080p), sin facecam, sin gancho y sin cortes;
subtítulos abajo y `.srt` aparte. La transcripción avanza por bloques de Whisper con progreso.

Cambia cualquier ajuste y pulsa **💾 Guardar preset** (o escribe un nombre nuevo para crear otro).
La transcripción se guarda en caché: re-renderizar el mismo vídeo con otros ajustes no vuelve a transcribir.

### Pistas de audio (OBS)
- **Pista del micro**: la que se transcribe (0 = primera). La vista previa muestra las pistas del archivo.
- **Pista del vídeo final**: un índice o `mix` (mezcla todas).

## Estructura

```
app.py                  Interfaz Gradio
pipeline/
  paths.py              Rutas y localización de ffmpeg (bin/ → PATH)
  media.py              ffprobe, extracción de audio/fotogramas, detección de NVENC
  transcribe.py         faster-whisper (CUDA int8_float16 → CPU int8 de respaldo) + caché
  subtitles.py          Estilos Frase/Resaltado/Karaoke, título gancho, .ass y .srt
  keywords.py           Palabras clave a resaltar (heurística, sin LLM)
  layout.py             Layouts Short (esquina/split/blur) y Largo, filtergraph de FFmpeg
  facedetect.py         Autodetección de la facecam (OpenCV; MediaPipe si trae la API clásica)
  render.py             Comando FFmpeg de un solo pase con progreso
  job.py                Orquestación y medición de tiempos
  presets.py            Presets JSON con valores por defecto
  timing.py             Cronómetro por pasos
presets/*.json          gta, eafc27, thefinals, minecraft, largo_youtube
tools/setup_assets.py   Descarga ffmpeg.exe y fuentes
tests/                  pytest (incluye render real con FFmpeg y palabras simuladas)
```

## Desarrollo

```bash
python -m pip install -r requirements.txt pytest
python -m pytest
```

## Hoja de ruta
- ~~Fase 2~~: estilos Resaltado, Karaoke y Título gancho; tabla de revisión; editor de presets;
  Modo Largo con .srt; layouts split/blur. ✅
- **Fase 3**: corte de silencios, emojis (Twemoji), auto-zoom, efectos de sonido, barra de progreso,
  marca de agua, Demucs, LLM (Ollama/Claude/OpenAI: corrección, resaltado, gancho, títulos y
  hashtags), lotes avanzados.
- **Fase 4** (opcional): subida a YouTube, Instagram y TikTok (borrador).
