# audiovisual-ia

Plataforma de producción audiovisual con inteligencia artificial (2D y 3D) basada en *Prompt-to-Video*.

El usuario describe una idea en lenguaje natural y la plataforma la transforma en un video 2D completo: guion, escenas, storyboard, imágenes, voz (TTS), música y efectos, subtítulos sincronizados, línea de tiempo y renderizado final en MP4.

> Estado actual: **Fase 1 (MVP 2D) — andamiaje (FEAT-001)**. El monorepo ya tiene su esqueleto con baseline verde (build/lint/test): backend FastAPI (`/health`), frontend Next.js que consulta ese estado, y proyecto Remotion mínimo. La lógica de negocio (datos, API, motores IA/imagen/voz, timeline, render, editor) se implementa tarea por tarea en FEAT-002..007 a partir de `.agents/tasks/`.

## Principios

- **Arquitectura modular por motores.** Seis motores independientes detrás de interfaces (ver `docs/ARCHITECTURE.md`): IA/guion, visual 2D, animación 2D avanzada, 3D/Blender, voz/audio, composición/render.
- **Proveedores conmutables.** Cada capacidad de IA (LLM, imágenes, voz) se consume detrás de una interfaz con *backends* intercambiables. El camino por defecto es **local/gratuito**, sin claves de pago.
- **Desarrollo por fases.** Fase 1 = MVP 2D funcional. Las fases 2 (animación 2D), 3 (3D/Blender) y 4 (comercial) encajan sin reescribir el núcleo.
- **Funcionalidad real.** Sin botones decorativos ni datos simulados presentados como terminados (sección 9 del prompt maestro).

## Stack (MVP / Fase 1)

| Capa | Tecnología |
|------|-----------|
| Frontend | Next.js + TypeScript + Tailwind CSS |
| Backend | Python + FastAPI (REST) |
| Audiovisual | Remotion (composición/render) + FFmpeg (media/mezcla) |
| LLM (por defecto) | Ollama local (`llama3.1:8b`) |
| Imágenes (por defecto) | Proveedor conmutable, backend local/stock gratuito (Pexels/Pixabay + placeholders generados) |
| Voz/TTS (por defecto) | Kokoro (offline) o edge-tts, reutilizados de lemo-opuscar |
| Subtítulos | faster-whisper (local) |

## Estructura del monorepo

```
audiovisual-ia/
  backend/     # API FastAPI (app/{api,models,engines,providers,jobs,storage}) + tests
  frontend/    # Next.js (App Router) + TypeScript + Tailwind
  remotion/    # proyecto Remotion (composiciones que leen el timeline JSON)
  docs/        # arquitectura, plan por fases, stack local/gratuito
  .env.example # variables de configuración (AIV_*)
```

## Arranque local

Requisitos: Node 20+, Python 3.11, y un FFmpeg accesible (ruta configurable vía `AIV_FFMPEG_BIN`).

```bash
# 0) Configuración
cp .env.example .env            # ajusta AIV_OLLAMA_MODEL, AIV_FFMPEG_BIN, claves opcionales…

# 1) Backend (FastAPI)  ->  http://localhost:8000
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000    # GET /health -> {"status":"ok"}

# 2) Frontend (Next.js)  ->  http://localhost:3000
cd frontend && npm install && npm run dev

# 3) Remotion (composición/render)
cd remotion && npm install && npm run studio
```

El LLM por defecto es **Ollama** (`AIV_OLLAMA_MODEL=llama3.1:8b`); arráncalo aparte con `ollama serve`.
No es necesario para el baseline de FEAT-001, pero sí a partir de FEAT-003.

## Documentación

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — arquitectura modular, capa de proveedores, modelo de datos, colas, config, cómo encajan las fases futuras.
- [`docs/PHASE-1-MVP.md`](docs/PHASE-1-MVP.md) — alcance del MVP, pipeline extremo a extremo, desglose de tareas con criterios de aceptación y enfoque de pruebas.
- [`docs/LOCAL-FREE-STACK.md`](docs/LOCAL-FREE-STACK.md) — modo local/gratuito, proveedores por defecto y opciones de pago pluggables, variables de entorno.
- [`docs/REFERENCE-REPOS-LICENSES.md`](docs/REFERENCE-REPOS-LICENSES.md) — análisis y licencias de los repos de referencia.
- [`docs/RISKS-ASSUMPTIONS.md`](docs/RISKS-ASSUMPTIONS.md) — riesgos y supuestos (Remotion comercial, cómputo local, sandbox, APIs de stock).

## Repos de referencia

Analizados como referencia de arquitectura y, cuando la licencia lo permite, como fuente de componentes reutilizables. No se copian proyectos completos; la arquitectura es propia e independiente. Ver `docs/REFERENCE-REPOS-LICENSES.md`.
