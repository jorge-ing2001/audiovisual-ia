# Arquitectura

Este documento describe la arquitectura modular de la plataforma: los seis motores, la capa de abstracción de proveedores que hace intercambiables los backends de IA, el modelo de datos, el sistema de trabajos en segundo plano, el diseño de configuración, y cómo encajan las fases 2, 3 y 4 sin reescribir el núcleo.

La arquitectura se diseña completa desde el inicio, pero **solo la Fase 1 (MVP 2D) se implementa ahora**. El resto son puntos de extensión definidos (interfaces, carpetas, tipos de job) que quedan listos para rellenar.

## 1. Visión de alto nivel

```
┌──────────────────────────────────────────────────────────────┐
│ Frontend (Next.js + TS + Tailwind)                             │
│  Dashboard · Proyectos · Editor de prompt · Storyboard ·       │
│  Editor de línea de tiempo · Panel de render · Historial       │
└───────────────┬────────────────────────────────────────────────┘
                │ REST (JSON) + polling/SSE de estado de jobs
┌───────────────▼────────────────────────────────────────────────┐
│ Backend API (Python + FastAPI)                                  │
│  Routers: projects, scenes, assets, generation, jobs, render    │
└───────────────┬────────────────────────────────────────────────┘
                │ encola trabajos
┌───────────────▼────────────────────────────────────────────────┐
│ Orquestador de jobs (cola + workers)                            │
│  pipeline prompt→video, reintentos, estados, por-escena         │
└───────────────┬────────────────────────────────────────────────┘
                │ invoca motores a través de interfaces
┌───────────────▼────────────────────────────────────────────────┐
│ MOTORES (cada uno detrás de una interfaz)                       │
│  1. IA/guion   2. Visual 2D   3. Anim 2D (F2)                   │
│  4. 3D/Blender (F3)   5. Voz/Audio   6. Composición/Render      │
└───────────────┬────────────────────────────────────────────────┘
                │ cada motor usa proveedores conmutables
┌───────────────▼────────────────────────────────────────────────┐
│ CAPA DE PROVEEDORES (provider registry)                         │
│  LLMProvider · ImageProvider · TTSProvider · ASRProvider        │
│  default = local/gratuito; alternativas de pago pluggables      │
└───────────────┬────────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────────────────┐
│ Almacenamiento: BD (proyectos/escenas/assets/versiones) +       │
│ sistema de archivos de media (workspace por proyecto)           │
└──────────────────────────────────────────────────────────────────┘
```

Separación clave: **el frontend nunca llama a un proveedor**. Llama a la API; la API encola jobs; los jobs invocan motores; los motores invocan proveedores a través del *registry*. Cambiar de proveedor (p. ej. de stock gratuito a Stable Diffusion local o a una API de pago) no toca ni la API ni el frontend.

## 2. Los seis motores

Cada motor es un paquete Python con una interfaz pública estable (una clase abstracta o `Protocol`) y una o más implementaciones. Los motores no se conocen entre sí; se comunican a través de los artefactos del modelo de datos (guion → escenas → assets → timeline).

| # | Motor | Responsabilidad | Fase | Interfaz (borrador) |
|---|-------|-----------------|------|---------------------|
| 1 | **IA / guion** | Interpretar prompt, generar guion, dividir en escenas, storyboard, personajes, tono/estructura | 1 | `ScriptEngine.generate_script(prompt, options) -> Script` |
| 2 | **Visual 2D** | Obtener/generar una imagen por escena (stock, placeholder, SD local, API) | 1 | `VisualEngine.render_scene_image(scene, style) -> ImageAsset` |
| 3 | **Animación 2D avanzada** | Personajes animados, cámara, parallax, lip-sync, transiciones | 2 | `Animation2DEngine.animate(scene, directives) -> AnimatedClip` |
| 4 | **3D / Blender** | Modelos, escenarios, cámaras, render de secuencias vía scripts Blender | 3 | `Engine3D.render_sequence(scene3d) -> VideoClip` |
| 5 | **Voz / Audio** | TTS multivoz, música de fondo, SFX, mezcla y niveles | 1 | `VoiceEngine.synthesize(lines) -> list[AudioAsset]`; `AudioMixer.mix(...)` |
| 6 | **Composición / Render** | Ensamblar timeline JSON, componer en Remotion, muxear con FFmpeg, exportar MP4 | 1 | `RenderEngine.compose(timeline) -> VideoFile` |

Los motores 3, 4 (fases 2 y 3) se declaran como interfaces y carpetas vacías con un `NotImplementedError` claro; no se implementan ahora. El orquestador del pipeline solo invoca motores cuyo tipo de producción esté disponible.

## 3. Capa de proveedores (el corazón de la conmutabilidad)

Cada capacidad de IA se expresa como una interfaz con un *registry* que resuelve el backend activo por configuración (variable de entorno o campo del proyecto). Patrón:

```python
# backend/app/providers/base.py  (borrador conceptual)
class LLMProvider(Protocol):
    def complete(self, prompt: str, *, system: str | None = None,
                 json_schema: dict | None = None, **opts) -> str: ...

class ImageProvider(Protocol):
    def image_for(self, query: str, *, aspect: str, out_dir: str,
                  seed: int | None = None) -> ImageAsset: ...

class TTSProvider(Protocol):
    def synthesize(self, lines: list[VoiceLine], out_dir: str) -> list[AudioAsset]: ...

class ASRProvider(Protocol):
    def transcribe(self, audio_path: str) -> list[WordTiming]: ...
```

Un *registry* mapea un nombre de backend a su factoría:

```python
PROVIDERS = {
    "llm":   {"ollama": OllamaLLM, "openai": OpenAILLM},        # default: ollama
    "image": {"stock": StockImageProvider, "placeholder": PlaceholderProvider,
              "sd_local": StableDiffusionLocal, "stability": StabilityAPI},  # default: stock→placeholder
    "tts":   {"kokoro": KokoroTTS, "edge": EdgeTTS, "elevenlabs": ElevenLabsTTS,
              "openai": OpenAITTS},  # default: kokoro (offline), fallback edge
    "asr":   {"faster_whisper": FasterWhisper},                 # default: faster_whisper
}
```

Reglas:

- **Defaults locales/gratuitos.** El sistema arranca y produce un video sin ninguna clave de pago. Ver `LOCAL-FREE-STACK.md`.
- **Degradación.** El `ImageProvider` por defecto (`stock`) cae a `placeholder` si no hay red o no hay clave de stock; así el pipeline nunca se bloquea por falta de imágenes. El `TTSProvider` por defecto (`kokoro`, offline) cae a `edge` si el modelo offline no está disponible.
- **Selección.** Backend activo por capacidad vía env var (`AIV_LLM_BACKEND`, `AIV_IMAGE_BACKEND`, ...) y, opcionalmente, override por proyecto en BD.
- **Contrato estable.** Añadir un proveedor de pago (ElevenLabs, OpenAI, Stability) es implementar la interfaz y registrarlo; no cambia motores, API ni frontend.

Los adaptadores de voz/subtítulos reutilizan los scripts MIT de lemo-opuscar (ver `REFERENCE-REPOS-LICENSES.md`): `KokoroTTS` y `EdgeTTS` envuelven `core/tts/tts.py` y `tts_zh.py`; `FasterWhisper` envuelve `core/tts/asr_check.py`; la mezcla final reutiliza el enfoque de `core/render/mux.sh` y la exportación SRT de `core/render/srt.py`.

## 4. Pipeline Prompt-to-Video (Fase 1)

Secuencia implementada como un job orquestado con pasos idempotentes y reanudables por escena:

```
prompt
  → [IA] guion narrativo (estructura, tono, duración objetivo)
  → [IA] división en escenas (texto, narración, duración, descripción visual)
  → [IA] storyboard (prompt/consulta visual + notas de cámara por escena)
  → [Visual 2D] imagen por escena (stock/placeholder por defecto)
  → [Voz] TTS de la narración por escena (+ dur.json de tiempos)
  → [Voz] música de fondo + SFX, mezcla a -14 LUFS
  → [Subtítulos] faster-whisper alinea palabras → cues → SRT
  → [Timeline] ensamblar timeline JSON (escenas, assets, tiempos, subtítulos)
  → [Render] Remotion compone el video
  → [Render] FFmpeg muxea video + mezcla → MP4 final
```

Cada paso escribe su artefacto al workspace del proyecto y actualiza el estado en BD, de modo que el editor puede intervenir entre pasos (editar texto, regenerar una escena, cambiar duración) y relanzar solo lo necesario.

## 5. Modelo de datos

Entidades mínimas del MVP (SQLite por defecto en local; diseño agnóstico para migrar a Postgres):

- **Project**: `id`, `name`, `prompt`, `production_type` (`2d` ahora; `3d` futuro), `aspect_ratio`, `resolution`, `status`, `created_at`, `provider_overrides` (JSON).
- **Script**: `id`, `project_id`, `structure`, `tone`, `language`, `raw_text`, `version`.
- **Scene**: `id`, `project_id`, `order`, `narration_text`, `visual_prompt`, `duration_s`, `camera_notes`, `status`.
- **Asset**: `id`, `project_id`, `scene_id` (nullable), `kind` (`image`|`voice`|`music`|`sfx`|`video`|`srt`), `path`, `provider`, `meta` (JSON), `created_at`.
- **Timeline**: `id`, `project_id`, `json` (línea de tiempo serializada que consume Remotion), `version`.
- **Version**: historial por entidad editable (`Script`, `Scene`, `Timeline`) para deshacer/rehacer y reproducibilidad (requisito sección 8: evitar regeneraciones innecesarias).
- **Job**: `id`, `project_id`, `type`, `status` (`pending|running|succeeded|failed`), `progress`, `error`, `retries`, `payload`, `result`.

Reutilización de recursos: antes de llamar a un proveedor, el motor comprueba si existe un `Asset` válido para esa escena/versión (cache por hash de entrada) y lo reutiliza. Esto cumple el requisito de no hacer llamadas costosas innecesarias.

## 6. Trabajos en segundo plano y colas

- **MVP local**: cola en proceso respaldada por la BD (tabla `Job`) con uno o varios workers `asyncio`/hilos. Sin dependencia de infraestructura externa; arranca con `uvicorn` + un worker.
- **Interfaz de cola** (`JobQueue.enqueue/claim/complete/fail`) para poder cambiar a Redis/RQ o Celery en Fase 4 sin tocar los motores.
- **Granularidad por escena**: generación de imagen/voz por escena son jobs independientes → paralelizables y re-ejecutables de forma aislada.
- **Reintentos y errores**: backoff y límite de reintentos por job; el estado y el mensaje de error se exponen en la API para el panel de render del frontend.

## 7. Configuración y variables de entorno

- Config centralizada con Pydantic Settings; todas las claves documentadas en `.env.example` (ver `LOCAL-FREE-STACK.md`).
- Selección de backend por capacidad mediante `AIV_*_BACKEND`.
- Rutas de binarios externos configurables (`AIV_FFMPEG_BIN`, `AIV_OLLAMA_URL`), porque en el sandbox FFmpeg vive en `/projects/sandbox/.localbin`.
- Las claves de pago (OpenAI, ElevenLabs, Stability, Pexels/Pixabay) son **opcionales**; su ausencia activa los defaults locales/gratuitos.

## 8. Estructura de directorios propuesta

```
audiovisual-ia/
  docs/                      # esta documentación
  backend/                   # FastAPI
    app/
      main.py                # creación de la app, routers
      config.py              # Pydantic Settings
      api/                   # routers REST (projects, scenes, assets, generation, jobs, render)
      models/                # modelos ORM + esquemas Pydantic
      engines/               # los 6 motores
        script/              # motor 1 (IA/guion)
        visual2d/            # motor 2 (visual 2D)
        animation2d/         # motor 3 (F2, interfaz + NotImplementedError)
        engine3d/            # motor 4 (F3, interfaz + NotImplementedError)
        voice/               # motor 5 (voz/audio)
        render/              # motor 6 (composición/render)
      providers/             # capa de proveedores (base + implementaciones + registry)
      jobs/                  # cola, worker, pipeline prompt→video
      storage/               # BD + workspace de media por proyecto
    tests/                   # pytest por módulo
    pyproject.toml
  frontend/                  # Next.js + TS + Tailwind
    app/                     # rutas (dashboard, project, editor, render)
    components/              # componentes reutilizables
    lib/api.ts               # cliente REST tipado
  remotion/                  # proyecto Remotion (composiciones que leen el timeline JSON)
  .env.example
```

## 9. Cómo encajan las fases futuras (sin reescritura)

- **Fase 2 — Animación 2D avanzada.** Se implementa `Animation2DEngine` (motor 3). El timeline ya soporta clips por escena; un clip puede ser una imagen estática (F1) o un clip animado (F2). El `production_type` del proyecto gana variantes; la API y el modelo de datos no cambian de forma.
- **Fase 3 — 3D/Blender.** Se implementa `Engine3D` (motor 4) como un proveedor de clips que corre Blender en modo headless vía scripts, exponiendo la misma interfaz "escena → clip de video". El orquestador elige motor 2D o 3D según `production_type`. Remotion/FFmpeg siguen siendo la capa de composición final.
- **Fase 4 — Comercial.** Se añaden: usuarios/auth, planes y límites de generación (middleware sobre los jobs), cola distribuida (misma interfaz `JobQueue`), biblioteca de personajes/escenarios (nuevas entidades), generación por lotes (N proyectos encolados), multilingüe (ya soportado por TTS/ASR), nuevos proveedores de IA (solo registrar). El modelo de versiones ya existe desde F1.

El invariante que lo hace posible: **todo lo específico de un modelo o proveedor vive detrás de una interfaz en `providers/` o en un motor; el resto del sistema solo conoce artefactos de datos.**
