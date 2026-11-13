# Fase 1 — MVP de generación de video 2D

Alcance: una aplicación funcional que convierte un prompt en un **video 2D completo** (narrativa, imágenes, locución, subtítulos y audio sincronizado), con un editor básico para modificar escenas, imágenes, textos, narraciones y duración antes del render definitivo.

Fuera de alcance en Fase 1: animación 2D avanzada (Fase 2), 3D/Blender (Fase 3), funciones comerciales/billing (Fase 4). La arquitectura (ver `ARCHITECTURE.md`) deja esos puntos de extensión listos.

## Pipeline extremo a extremo (objetivo del MVP)

```
prompt → [IA] guion → escenas → storyboard
       → [Visual 2D] imagen por escena (stock/placeholder)
       → [Voz] TTS por escena (+ tiempos) → música/SFX → mezcla −14 LUFS
       → [Subtítulos] faster-whisper → cues → SRT
       → [Timeline] JSON → [Render] Remotion compone → FFmpeg muxea → MP4
```

## Modo por defecto

Local/gratuito, sin claves de pago. LLM = Ollama (`llama3.2`); imágenes = stock gratuito con degradación a placeholder; TTS = Kokoro offline con fallback edge-tts; subtítulos = faster-whisper; render = Remotion + FFmpeg. Detalles en `LOCAL-FREE-STACK.md`.

## Desglose de tareas

El trabajo se implementa tarea por tarea. Cada tarea deja el repo en estado funcional. Las tareas están materializadas como features en `.agents/tasks/task-fase1-mvp-2d/features/` con pasos, criterios de aceptación y verificación. Resumen:

### FEAT-001 — Setup y andamiaje (chore)
Scaffolding del monorepo (backend FastAPI + frontend Next.js + proyecto Remotion), manifiestos de dependencias, `.env.example`, baseline de build/test verde. Reutiliza lemo-opuscar vía ruta configurable.
Aceptación: `pytest` corre (aunque con 1 test trivial), `next build`/lint pasan, `uvicorn` levanta `/health`.

### FEAT-002 — Núcleo backend: modelo de datos, API, cola de jobs y capa de proveedores (feat)
Modelos ORM (Project/Script/Scene/Asset/Timeline/Version/Job), routers REST, cola de jobs respaldada por BD con worker, y la capa de proveedores con interfaces + registry + defaults locales.
Aceptación: CRUD de proyectos por REST; encolar un job no-op y verlo pasar a `succeeded`; registry resuelve backend por env var.

### FEAT-003 — Motores IA/guion y Visual 2D con proveedores locales (feat)
`ScriptEngine` (Ollama) genera guion→escenas→storyboard en JSON estructurado; `VisualEngine` con `StockImageProvider` (Pexels/Pixabay) y `PlaceholderProvider` (Pillow) con degradación. Cache de assets por hash.
Aceptación: desde un prompt se obtienen escenas con `narration_text`/`visual_prompt`/`duration_s` y una imagen por escena (placeholder sin red/clave).

### FEAT-004 — Motor Voz/Audio y subtítulos (reutiliza lemo-opuscar MIT) (feat)
`VoiceEngine` con `KokoroTTS`/`EdgeTTS` envolviendo `core/tts/*`; `AudioMixer` con el enfoque de `mux.sh`; subtítulos con `FasterWhisper` + export SRT estilo `srt.py`.
Aceptación: una lista de líneas produce WAV por escena + `dur.json`; se genera un `.srt` alineado; la mezcla sale a ~−14 LUFS.

### FEAT-005 — Timeline + render Remotion + export MP4 (feat)
Ensamblado del timeline JSON; composición Remotion que lee el timeline (imagen + subtítulos + audio por escena); mux final con FFmpeg a MP4.
Aceptación: dado un timeline de ejemplo con assets reales, `remotion render` + mux producen un MP4 reproducible con imagen, voz y subtítulos sincronizados.

### FEAT-006 — Orquestación prompt→video end-to-end (feat)
Job de pipeline que encadena FEAT-003..005 por escena, con estados y reintentos, expuesto por la API (`POST /projects/{id}/generate`, polling de job).
Aceptación: un solo prompt, en modo local/gratuito, produce un MP4 final; el estado del job progresa y es consultable.

### FEAT-007 — Frontend MVP y editor básico (feat)
Dashboard, creación de proyecto, editor de prompt, vista de storyboard/escenas, edición de texto/narración/duración y regeneración por escena, panel de render con estado y descarga del MP4.
Aceptación: flujo completo desde el navegador: crear proyecto → prompt → generar → editar una escena y regenerar → renderizar → descargar MP4. Sin botones decorativos.

## Enfoque de pruebas por módulo

- **Proveedores**: tests de contrato por interfaz; el registry se prueba con un backend *fake* y con la degradación (stock→placeholder, kokoro→edge). Los proveedores de red se prueban con el cliente HTTP mockeado, pero el `PlaceholderProvider` se ejerce de verdad (genera un PNG real).
- **Motores**: `ScriptEngine` con respuesta de LLM mockeada valida el parseo a escenas; `VisualEngine` valida que escribe un asset en disco; `VoiceEngine`/subtítulos se prueban con un WAV corto real (Kokoro si está, si no se marca skip con razón) para no simular resultados; `AudioMixer` valida que el MP4/WAV de salida existe y mide LUFS con ffprobe.
- **API/cola**: tests de FastAPI (`TestClient`) para CRUD y para el ciclo de vida de un job real (encolar→ejecutar→succeeded) y de un job que falla→reintenta.
- **Render**: un test de integración produce un MP4 a partir de un timeline mínimo con assets de prueba reales y verifica duración/streams con ffprobe (se salta con razón si Remotion/FFmpeg no están en el entorno de CI).
- **End-to-end**: un test marca el pipeline completo en modo placeholder+edge/kokoro; si el LLM (Ollama) no está, se inyecta un guion de prueba **etiquetado como fixture**, nunca presentado como salida real.

Principio (sección 9): funcionalidades reales, sin datos simulados presentados como terminados; cada test ejerce el código que cubre y fallaría si ese código se revierte.
