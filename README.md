# 🎮 ClipForge

App **local** para Windows (Python 3.11) que edita y subtitula vídeos de gaming en español.
Todo offline y gratis: faster-whisper + FFmpeg + Gradio en `http://127.0.0.1:7860`.

> **Estado: completo (Fase 3)** — Modo Short 1080x1920 (layouts Facecam esquina, Split, Blur y Sin facecam) y
> Modo Largo 16:9 con .srt; estilos Frase, Resaltado y Karaoke; Título gancho; **editor de subtítulos**
> para corregir el texto a mano; corte de silencios, auto-zoom, efectos de sonido, barra de progreso,
> marca de agua, aislar voz (Demucs), LLM opcional (Ollama/Claude/OpenAI), procesado por lotes y
> motor de render opcional **Remotion** para Shorts con animaciones avanzadas.

## Arranque rápido (Windows)

1. Instala Python 3.11 (`winget install Python.Python.3.11`).
2. Doble clic en **`run.bat`**. La primera vez:
   - crea `.venv` e instala `requirements.txt` (y `requirements-gpu.txt` si detecta una NVIDIA),
   - descarga `ffmpeg.exe`/`ffprobe.exe` en `bin/` y las fuentes libres en `assets/fonts/`,
   - abre el navegador.
3. El modelo Whisper (`large-v3-turbo`, ~1,6 GB) se descarga en `models/` la primera vez que transcribes.
4. *(Opcional)* Para el motor **Remotion** instala Node.js (`winget install OpenJS.NodeJS.LTS`) y vuelve a
   abrir `run.bat`: instala Remotion en `remotion/` y su navegador (~300 MB) una sola vez.

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
5. **Revisar y corregir** (opcional): *Transcribir para revisar* y luego:
   - **Subtítulos (frases)**: cada fila es un subtítulo tal y como sale en pantalla. Cambia el texto
     para corregir errores (si mantienes el nº de palabras, el karaoke conserva los tiempos exactos;
     si no, se reparten dentro de la frase), ajusta inicio/fin o vacía el texto para borrarlo.
   - **Palabras**: ajuste fino de cada palabra, qué se resalta y a qué frase pertenece.
   - *↻ Reagrupar* rehace las frases con el estilo actual; *🤖 Pasar por el LLM* corrige y propone.
   - Gancho y textos para publicar (títulos/descripción/hashtags) editables por vídeo.
6. **Efectos** (cada uno con su interruptor, guardable en el preset):
   - *Cortar silencios*: por umbral de dB en la pista del micro o por huecos entre palabras; margen
     ajustable. Corta vídeo y audio en el mismo pase y reajusta los subtítulos.
   - *Auto-zoom*: punch-in sobre el gameplay en palabras clave y/o gritos (volumen sobre lo normal).
   - *Efectos de sonido*: al hacer zoom, al salir el gancho y/o en cada corte. Vienen 4 sonidos
     generados (whoosh, boom, pop, swish); añade los tuyos en `assets/sfx/`.
   - *Barra de progreso* (arriba/abajo, color y grosor) y *marca de agua* con tu @handle.
   - *Aislar voz (Demucs)*: limpia la voz antes de transcribir (`pip install demucs`, opcional).
7. **IA (LLM)** (opcional): Ninguno · Ollama (local, gratis) · Claude API · OpenAI API. Corrige
   palabras sin tocar tiempos, elige palabras clave, propone el gancho y escribe títulos, descripción
   y hashtags por plataforma (van a `_meta.txt`). Claves en `.env`.
8. **Lotes**: además de arrastrar vídeos puedes indicar una carpeta (con subcarpetas), saltar los ya
   procesados y obtener un resumen `output/lote_*.csv`. Si un vídeo falla, el lote sigue.
9. **Motor de render** (solo Shorts): *FFmpeg* (rápido, por defecto) o *Remotion* (animaciones más
   suaves: las frases entran con pop/deslizamiento/fundido, el gancho rebota, el zoom va con curvas).
   Usa los mismos ajustes; es bastante más lento (minutos en vez de segundos por clip). Si Node.js o
   Remotion no están instalados, se usa FFmpeg y se avisa en el registro. El Modo Largo siempre usa FFmpeg.
10. **🚀 Procesar todos**. Los archivos salen en `output/`:

| Archivo | Contenido |
|---|---|
| `<nombre>_short.mp4` / `<nombre>_largo.mp4` | H.264 (NVENC si hay NVIDIA, AMF si hay AMD, si no x264 CRF 20), AAC 48 kHz, fps del original |
| `<nombre>.ass` | Subtítulos quemados (editables con Aegisub) |
| `<nombre>.srt` | Subtítulos aparte, en líneas más largas y con el texto original (para YouTube) |
| `<nombre>_words.json` | Palabras con tiempos, probabilidad y marca de resaltado |
| `<nombre>_meta.txt` | Resumen, **tiempo de cada paso** y textos para publicar (LLM) |
| `lote_*.csv` | Resumen del lote (duraciones, cortes, zooms, errores) |
| `<nombre>_ffmpeg.log` | Comando y log de FFmpeg (para depurar) |

**Modo Largo**: mantiene el 16:9 (resolución original o 1080p), sin facecam, sin gancho y sin cortes por defecto;
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
  media.py              ffprobe, extracción de audio/fotogramas, detección de NVENC/AMF
  transcribe.py         faster-whisper (CUDA int8_float16 → CPU int8 de respaldo) + caché
  subtitles.py          Estilos Frase/Resaltado/Karaoke, título gancho, .ass y .srt
  keywords.py           Palabras clave a resaltar (heurística, sin LLM)
  layout.py             Layouts Short (esquina/split/blur) y Largo, filtergraph de FFmpeg
  facedetect.py         Autodetección de la facecam (OpenCV; MediaPipe si trae la API clásica)
  phrases.py            Edición de subtítulos por frase (texto/tiempos → palabras)
  silence.py            Corte de silencios y reajuste de tiempos
  effects.py            Auto-zoom: volumen por palabra, gritos y expresión de zoompan
  sfx.py                Efectos de sonido (generación y eventos)
  voice.py              Aislar voz con Demucs (opcional)
  llm.py                Ollama / Claude / OpenAI con salida JSON
  remotion_render.py    Motor opcional Remotion: vídeo base + props JSON + CLI
  render.py             Comando FFmpeg de un solo pase con progreso
  job.py                Orquestación y medición de tiempos
  presets.py            Presets JSON con valores por defecto
  timing.py             Cronómetro por pasos
presets/*.json          gta, eafc27, thefinals, minecraft, largo_youtube
remotion/                Proyecto Remotion (React): composición ClipShort (layouts, subtítulos, gancho…)
tools/setup_assets.py   Descarga ffmpeg.exe y fuentes
tests/                  pytest (incluye render real con FFmpeg y palabras simuladas)
```

## Desarrollo

```bash
python -m pip install -r requirements.txt pytest
python -m pytest
# render real con Remotion (lento): CLIPFORGE_TEST_REMOTION=1 python -m pytest tests/test_remotion.py
# editar la composición en vivo: cd remotion && npm run studio
```

## Hoja de ruta
- ~~Fase 2~~: estilos Resaltado, Karaoke y Título gancho; tabla de revisión; editor de presets;
  Modo Largo con .srt; layouts split/blur. ✅
- ~~Fase 3~~: editor de subtítulos, corte de silencios, auto-zoom, efectos de sonido, barra de
  progreso, marca de agua, Demucs, LLM y lotes. ✅ (Emojis automáticos descartados.)
- ~~Fase 4~~: subida automática a redes, descartada: la publicación se hace a mano con los
  archivos de `output/` (vídeo, `.srt` y textos de `_meta.txt`).
