# Stack local / gratuito (modo por defecto)

El MVP debe ejecutarse en modo **local/gratuito**, sin ninguna clave de API de pago. Los proveedores de pago existen como *backends* pluggables detrás de las interfaces de `providers/`, pero el camino por defecto usa herramientas libres y locales.

## Defaults por capacidad

| Capacidad | Backend por defecto | Alternativas pluggables (opcionales) |
|-----------|---------------------|--------------------------------------|
| LLM (guion) | **Ollama** local (p. ej. `llama3.2`) vía `http://localhost:11434` | OpenAI, cualquier endpoint compatible |
| Imágenes | **Proveedor conmutable**: `stock` (Pexels/Pixabay, clave gratuita) con **degradación a `placeholder` generado** si no hay red/clave | Stable Diffusion local (`sd_local`), Stability API, OpenAI Images |
| Voz / TTS | **Kokoro** (offline, reutilizado de lemo-opuscar) con *fallback* a **edge-tts** (online, gratuito) | ElevenLabs, OpenAI TTS |
| Subtítulos / ASR | **faster-whisper** (local, reutilizado de lemo-opuscar) | — |
| Música / SFX | Bibliotecas libres de derechos (SFX de shorts-kit MIT) + síntesis local | APIs de música |
| Render / mezcla | **Remotion + FFmpeg** local | — |

Texto exacto del usuario para imágenes: proveedor "conmutable gratuito, con un backend local/stock gratuito" por defecto.

## Degradación garantizada

El pipeline nunca se bloquea por falta de un servicio de pago o de red:

- **Imágenes**: `stock` requiere una clave gratuita (Pexels/Pixabay) y red. Si falta cualquiera, el proveedor cae automáticamente a `placeholder`, que genera una imagen local (color/gradiente + texto de la escena) con Pillow. El video se produce igual.
- **LLM**: si Ollama no responde, el motor de guion devuelve un error claro y accionable (instrucción de arrancar Ollama), porque el guion es el primer eslabón; no se inventa contenido simulado.
- **TTS**: Kokoro es offline; si su modelo (~350 MB) no está, cae a edge-tts (necesita red). Si tampoco hay red, el job falla con mensaje claro.

## Variables de entorno (`.env.example`)

Todas opcionales salvo donde se indique. Sin ninguna clave de pago, el sistema funciona con los defaults.

```
# --- Selección de backend por capacidad ---
AIV_LLM_BACKEND=ollama            # ollama | openai
AIV_IMAGE_BACKEND=stock           # stock | placeholder | sd_local | stability | openai
AIV_TTS_BACKEND=kokoro            # kokoro | edge | elevenlabs | openai
AIV_ASR_BACKEND=faster_whisper

# --- LLM local ---
AIV_OLLAMA_URL=http://localhost:11434
AIV_OLLAMA_MODEL=llama3.2

# --- Imágenes (stock gratuito; claves gratuitas, opcionales) ---
AIV_PEXELS_API_KEY=
AIV_PIXABAY_API_KEY=

# --- Binarios externos ---
AIV_FFMPEG_BIN=/projects/sandbox/.localbin/ffmpeg
AIV_FFPROBE_BIN=/projects/sandbox/.localbin/ffprobe

# --- Reutilización de lemo-opuscar (voz/ASR/mux) ---
AIV_LEMO_OPUSCAR_PATH=/projects/sandbox/lemo-opuscar   # scripts MIT reutilizados

# --- Voz/Audio (motor 5) ---
AIV_TTS_VOICE=            # voz por defecto de Kokoro (vacío = af_bella / zf_xiaoxiao)
AIV_EDGE_VOICE=           # voz por defecto de edge-tts (vacío = zh-CN-XiaoxiaoNeural)
AIV_TTS_CACHE_HOME=       # HOME alternativo para la caché de espeak-ng de Kokoro (si $HOME es RO)
AIV_ASR_MODEL=base        # modelo de faster-whisper (nombre HF o ruta local)
AIV_ASR_LANGUAGE=         # idioma forzado para la transcripción (vacío = autodetección)

# --- Proveedores de pago (opcionales, solo si se activan arriba) ---
OPENAI_API_KEY=
ELEVENLABS_API_KEY=
STABILITY_API_KEY=

# --- Almacenamiento ---
AIV_DATABASE_URL=sqlite:///./audiovisual.db
AIV_MEDIA_ROOT=./media
```

## Restricciones del entorno (sandbox)

- El gestor de paquetes del sistema y `$HOME` son de solo lectura; FFmpeg 7.0.2 está como binario estático en `/projects/sandbox/.localbin`. Por eso las rutas de binarios son configurables.
- No hay GPU garantizada: **Stable Diffusion local y Ollama con modelos grandes no son fiables en el sandbox**, de ahí que el default de imágenes degrade a stock/placeholder y el LLM use un modelo pequeño.
- Node v22, npm, bun 1.2.14 y Python 3.11.15 están disponibles. Kokoro (kokoro-onnx) requiere Python 3.11–3.13: compatible.
