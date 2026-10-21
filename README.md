# 🎮 ClipForge

App **local** para Windows (Python 3.11) que edita y subtitula vídeos de gaming en español.
Todo offline y gratis: faster-whisper + FFmpeg + Gradio en `http://127.0.0.1:7860`.

> **Estado: Fase 1 (MVP)** — Modo Short 1080x1920, layout "Facecam esquina" (y "Sin facecam"),
> subtítulos estilo "Frase", render en un solo pase y tiempos medidos por paso.

## Arranque rápido (Windows)

1. Instala Python 3.11 (`winget install Python.Python.3.11`).
2. Doble clic en **`run.bat`**. La primera vez:
   - crea `.venv` e instala `requirements.txt` (y `requirements-gpu.txt` si detecta una NVIDIA),
   - descarga `ffmpeg.exe`/`ffprobe.exe` en `bin/` y las fuentes libres en `assets/fonts/`,
   - abre el navegador.
3. El modelo Whisper (`large-v3-turbo`, ~1,6 GB) se descarga en `models/` la primera vez que transcribes.

## Uso

1. Arrastra uno o varios vídeos y elige un **preset** (`gta`, `eafc27`, `thefinals`, `minecraft`).
2. Ajusta el **rectángulo de la facecam** (X/Y/Ancho/Alto en % del vídeo original) y pulsa
   **👁 Vista previa**: verás el fotograma original con la facecam (rojo) y el recorte 9:16 (azul),
   y el resultado final con un subtítulo de ejemplo.
3. **🚀 Procesar todos**. Los archivos salen en `output/`:

| Archivo | Contenido |
|---|---|
| `<nombre>_short.mp4` | Vídeo 1080x1920, H.264 (NVENC si hay NVIDIA, si no x264 CRF 20), AAC 48 kHz, fps del original |
| `<nombre>.ass` | Subtítulos quemados (editables con Aegisub) |
| `<nombre>.srt` | Subtítulos aparte (interruptor "Exportar .srt") |
| `<nombre>_words.json` | Palabras con tiempos de inicio/fin y probabilidad |
| `<nombre>_meta.txt` | Resumen y **tiempo de cada paso** |
| `<nombre>_ffmpeg.log` | Comando y log de FFmpeg (para depurar) |

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
  subtitles.py          Agrupado en frases, .ass y .srt, zonas seguras de TikTok
  layout.py             Geometría del layout y filtergraph (máscara redondeada + sombra)
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
- **Fase 2**: estilos Resaltado, Karaoke y Título gancho; tabla editable de la transcripción;
  editor de presets (marcar rectángulos sobre el fotograma); Modo Largo 16:9 con .srt; layouts split/blur.
- **Fase 3**: corte de silencios, emojis (Twemoji), auto-zoom, efectos de sonido, barra de progreso,
  marca de agua, Demucs, LLM (Ollama/Claude/OpenAI), lotes avanzados.
- **Fase 4** (opcional): subida a YouTube, Instagram y TikTok (borrador).
