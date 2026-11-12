# audiovisual-ia

Plataforma de producción audiovisual con inteligencia artificial (2D y 3D) basada en *Prompt-to-Video*.

El usuario describe una idea en lenguaje natural y la plataforma la transforma en un video 2D completo: guion, escenas, storyboard, imágenes, voz (TTS), música y efectos, subtítulos sincronizados, línea de tiempo y renderizado final en MP4.

> Estado actual: **planificación de la Fase 1 (MVP 2D)**. Este repositorio contiene, por ahora, la documentación de arquitectura, el plan por fases y el desglose de tareas. La implementación se hace tarea por tarea a partir de `.agents/tasks/`.

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
| LLM (por defecto) | Ollama local (p. ej. `llama3.2`) |
| Imágenes (por defecto) | Proveedor conmutable, backend local/stock gratuito (Pexels/Pixabay + placeholders generados) |
| Voz/TTS (por defecto) | Kokoro (offline) o edge-tts, reutilizados de lemo-opuscar |
| Subtítulos | faster-whisper (local) |

## Documentación

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — arquitectura modular, capa de proveedores, modelo de datos, colas, config, cómo encajan las fases futuras.
- [`docs/PHASE-1-MVP.md`](docs/PHASE-1-MVP.md) — alcance del MVP, pipeline extremo a extremo, desglose de tareas con criterios de aceptación y enfoque de pruebas.
- [`docs/LOCAL-FREE-STACK.md`](docs/LOCAL-FREE-STACK.md) — modo local/gratuito, proveedores por defecto y opciones de pago pluggables, variables de entorno.
- [`docs/REFERENCE-REPOS-LICENSES.md`](docs/REFERENCE-REPOS-LICENSES.md) — análisis y licencias de los repos de referencia.
- [`docs/RISKS-ASSUMPTIONS.md`](docs/RISKS-ASSUMPTIONS.md) — riesgos y supuestos (Remotion comercial, cómputo local, sandbox, APIs de stock).

## Repos de referencia

Analizados como referencia de arquitectura y, cuando la licencia lo permite, como fuente de componentes reutilizables. No se copian proyectos completos; la arquitectura es propia e independiente. Ver `docs/REFERENCE-REPOS-LICENSES.md`.
