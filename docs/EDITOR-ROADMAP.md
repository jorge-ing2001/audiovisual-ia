# Roadmap del Editor y motores complementarios

Este documento registra TODAS las funciones solicitadas para el editor de vídeo
integrado y los motores complementarios, organizadas en bloques priorizados. El
objetivo es no perder ninguna función y construir por capas (mejor una versión
funcional y estable que muchas opciones a medias). Cada función es **real y conectada
al backend** cuando se implemente; nada de controles decorativos.

La viabilidad está evaluada para el enfoque **local/gratuito** del proyecto
(Remotion + FFmpeg + faster-whisper + Blender + ComfyUI opcional) sobre el hardware
del usuario (RTX 3050 4 GB). "Calidad nivel CapCut" se tiende iterando.

Leyenda: ✅ viable · ✅ YA EXISTE (reutilizar) · ⚠️ viable con matices (coste/VRAM/complejidad).

---

## Orden de construcción propuesto

1. **BLOQUE 0 — Editor MVP** (ya planificado en `EDITOR-MVP-SCOPE.md`, FEAT-001..004). Base multipista: abrir timeline, operaciones esenciales de clip/audio/subtítulos, preview con Remotion Player (con audio), export re-render, undo/redo. **Se implementa primero.**
2. **BLOQUE 1 — Editor Avanzado** (interacción pro de edición).
3. **BLOQUE 2 — Subtítulos y Títulos Pro** (para cortometrajes).
4. **BLOQUE 3 — Motor ComfyUI** (imágenes IA locales, VRAM secuencial).
5. **BLOQUE 4 — Administración de proyectos y Control de Calidad.**

Cada bloque se planifica en features pequeñas con criterios de aceptación y se
verifica (pytest backend, build/lint/typecheck frontend/remotion, ffprobe en export)
antes de pasar al siguiente. Todos los bloques se montan SOBRE el modelo multipista
`EditProject` del BLOQUE 0 (ver `EDITOR-MULTITRACK-ARCHITECTURE.md`).

---

## BLOQUE 1 — Editor Avanzado

Ampliaciones de interacción sobre el editor multipista.

| Función | Viabilidad | Dónde / cómo |
|---|---|---|
| Arrastrar y soltar archivos (importar medios) | ✅ | Frontend drag&drop + endpoint de subida (hay uno para imágenes en FEAT-B; generalizar a audio/vídeo) → `Asset` + clip en pista |
| Ajuste magnético (Snap) | ✅ | Lógica de alineación en el editor (frontend) sobre bordes de clips/marcadores |
| Selección múltiple | ✅ | Estado de selección en el frontend; operaciones batch sobre el `EditProject` |
| Copiar/pegar atributos | ✅ | Copiar propiedades de un clip (transform, volumen, estilo) a otros |
| Bloquear pistas | ✅ | Propiedad `locked` por pista en `EditProject` |
| Ocultar / silenciar pistas | ✅ | Propiedades `hidden`/`muted` por pista; afectan preview y export |
| Desvincular audio y vídeo | ✅ | El modelo multipista ya separa pistas; operación de "detach" |
| Edición ripple (cerrar huecos al eliminar) | ✅ | Operación pura sobre el timeline |
| Marcadores | ✅ | Lista de marcadores (tiempo + etiqueta) en `EditProject` |
| Congelar fotograma | ✅ | Remotion (freeze frame) + rango de clip |
| Control de velocidad (acelerar/ralentizar/invertir) | ⚠️ | FFmpeg `setpts`/`atempo`; invertir (`reverse`) es pesado pero funciona |
| Varias secuencias por proyecto | ✅ | Extender: N `EditProject`/`Timeline` por proyecto (versiones/variantes del vídeo) |

---

## BLOQUE 2 — Subtítulos y Títulos Pro

Enfocado en cortometrajes. Varias piezas base YA existen.

| Función | Viabilidad | Dónde / cómo |
|---|---|---|
| Transcripción automática (Whisper local) | ✅ YA EXISTE | faster-whisper (FEAT-004), ASR por palabra |
| Sincronización de subtítulos por palabra | ✅ YA EXISTE | `palabras.json` (FIX-A), WordTiming |
| Subtítulos karaoke (resaltar palabra pronunciada) | ✅ | Render en Remotion usando los timings por palabra |
| Import/Export SRT, VTT, ASS | ✅ | SRT ya; añadir serializadores/parsers VTT y ASS |
| Plantillas de subtítulos | ✅ | Presets de estilo (fuente/tamaño/color/contorno/posición) |
| Títulos animados y créditos | ✅ | Remotion (su fortaleza): intros, lower-thirds, créditos finales |
| Tipografías personalizadas | ✅ | Fuentes empaquetadas en el proyecto (@remotion/google-fonts o locales) |
| Contornos, sombras y fondos de texto | ✅ | Estilo de subtítulo (ya hay base estilo TikTok); ampliar |
| Emojis y elementos gráficos | ✅ | Overlays (capas gráficas) en Remotion |
| Logotipos y marcas de agua | ✅ | Overlay de imagen posicionable (el usuario sube su logo) |
| Posicionamiento visual drag&drop | ✅ | Frontend: arrastrar el texto/overlay sobre el preview |
| Plantillas TikTok / Reels / Shorts | ✅ | Presets de formato 9:16 + zonas seguras + estilos |

---

## BLOQUE 3 — Motor ComfyUI (imágenes IA locales)

Nuevo backend conmutable del proveedor de imágenes. YA previsto en `.env.example`
(`AIV_SD_LOCAL_ENABLED`, `AIV_SD_LOCAL_BACKEND=comfyui`, `AIV_SD_LOCAL_URL`).

| Aspecto | Viabilidad | Nota |
|---|---|---|
| `ComfyUIImageProvider` conmutable | ✅ | Se registra como backend `sd_local` del proveedor de imágenes (capa de FEAT-002); no rompe `stock`/`placeholder` ni la vía de pago |
| Gestión de VRAM secuencial | ✅ | **Decisión del usuario:** generar guion primero (Ollama), LIBERAR VRAM (keep_alive=0 / descargar modelo), y DESPUÉS generar imágenes con ComfyUI. Nunca Ollama + ComfyUI a la vez. La cola de jobs (FEAT-002) ya es secuencial; se añade el paso explícito de liberar VRAM entre etapas |
| Calidad/velocidad en RTX 3050 4 GB | ⚠️ | SD 1.5 con `--lowvram`: lento pero posible. SDXL/Flux: muy justo/inviable en 4 GB. Documentar expectativas |
| Alcance | — | Solo pipeline 2D (imágenes fijas por escena). No resuelve animación (eso es Blender/vídeo IA futuro) |

---

## BLOQUE 4 — Administración de proyectos y Control de Calidad

| Función | Viabilidad | Dónde / cómo |
|---|---|---|
| Administración de proyectos | ✅ YA EXISTE (base) | BD (Project/Scene/Asset/Timeline/Version) + Dashboard. Enriquecer: duplicar, renombrar, carpetas/etiquetas, buscar, borrar |
| Herramientas de edición inteligente | ⚠️ | Algunas ya existen (auto-subtítulos, auto-mezcla −14 LUFS). Añadir: auto-corte por silencios (ya hay `trim.py`), sugerencias; evitar prometer "IA mágica" |
| Control de calidad | ✅ | Checks automáticos antes/después del export: loudness −14 LUFS (ya se mide), detección de frames negros, duración coherente, sincronía subtítulos/audio, resolución/aspect |
| Funciones de producción cinematográfica | ⚠️ | En 2D: planos/zoom limitados (Ken Burns, transforms). En 3D (Blender): cámaras cinematográficas reales (primer plano, travelling, aérea, contrapicado) — su terreno natural |

> Nota: "usuarios/login/multiusuario y planes de suscripción" NO están aquí: pertenecen a la
> Fase 4 comercial del plan original. Hoy la app es monousuario local (correcto para correr en el PC).

---

## Principios al implementar cada bloque

- Conservar TODOS los módulos y la arquitectura; no simplificar; mantener la vía futura de proveedores de pago (vídeo IA) conmutable.
- Cada función real y conectada al backend; sin controles decorativos ni datos simulados presentados como terminados.
- Verificación real por feature (pytest, build/lint/typecheck, ffprobe en export) antes de avanzar.
- Lo que no sea ejecutable en el sandbox (p. ej. ComfyUI/Blender/GPU) se verifica con lógica pura + skips con razón explícita, y se valida de verdad en el PC del usuario.
