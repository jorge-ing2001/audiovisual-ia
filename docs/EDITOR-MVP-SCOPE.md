# Editor multipista — Alcance del MVP vs. iteraciones futuras

> Estado: **PLAN**. Documento hermano de
> [`EDITOR-MULTITRACK-ARCHITECTURE.md`](EDITOR-MULTITRACK-ARCHITECTURE.md).
> Fija, con honestidad, qué entra en el **MVP del editor** (real, conectado y verificable)
> y qué queda para iteraciones posteriores. El objetivo es un *vertical slice*
> extremo a extremo que funcione de verdad antes de ampliar superficie.

---

## 1. Honestidad sobre el tamaño

Un editor de vídeo tipo CapCut es, por sí solo, tan grande como una fase entera de la
plataforma. Intentar entregar **todas** las funcionalidades (transiciones avanzadas,
keyframes de cámara, biblioteca de efectos, reencuadre fino, curvas de volumen, etc.) de
una vez produciría una UI amplia pero superficial, justo lo que el usuario rechaza
("no quiero únicamente cambios visuales"). Por eso el alcance se **fasea**: primero un
núcleo donde **cada operación es real** (persiste en el `EditProject`, se ve en el preview y
se refleja en el export), y después se amplía.

---

## 2. MVP del editor (lo que SÍ entra)

Todo lo del MVP cumple el criterio: **real, conectado al backend y verificable**.

### 2.1 Apertura e infraestructura
- Botón **«Editar vídeo»** visible tras la generación/composición (en `RenderPanel`/
  `ProjectWorkspace`), que lleva a `/projects/[id]/editor`.
- Derivación `Timeline` (por escena) → `EditProject` multipista al abrir, persistida en la
  tabla `Timeline` y versionada en `Version`.
- API de edición: open/get/put/op/undo/redo/history/export.

### 2.2 Línea de tiempo multipista
- Pistas independientes: **vídeo/imagen, voz, música, SFX, subtítulos**.
- Representación visual de clips posicionados en el tiempo por pista.

### 2.3 Operaciones de clips (escenas) — reales
- **Recortar (trim)**, **dividir (split)**, **eliminar**, **duplicar**, **reorganizar/
  mover**, **ajustar duración**.

### 2.4 Edición de audio — básica pero real
- **Volumen** por clip y por pista.
- **Recortar** y **reposicionar** voces; **música de fondo** y **SFX** como clips con
  posición y volumen. (Fades in/out si caben sin coste extra.)

### 2.5 Edición de subtítulos — real
- **Texto**, **tiempos** (inicio/duración), y **estilo**: tipografía, tamaño, color,
  posición y preset (classic/tiktok). Se refleja en preview y export.

### 2.6 Edición de imágenes/vídeos — básica
- **Reemplazar recurso** (elegir otro `Asset` del proyecto).
- **Duración** del clip.
- **Transform** básico: escala y posición (encuadre simple, fit cover/contain).

### 2.7 Vista previa en tiempo real
- **Remotion Player** (`@remotion/player`) con la composición `EditComposition`,
  alimentado por el `EditProject` del estado; **con audio** por pista (`<Audio>`).
- Cada edición se refleja en el Player sin recargar.

### 2.8 Exportación
- Job `edit_export` que **re-renderiza desde el `EditProject`** (no regenera desde el
  prompt), reutilizando `RenderEngine` (Remotion `EditComposition`) + `AudioMixer`
  (−14 LUFS) + mux FFmpeg. Produce el MP4 final descargable, **conservando la sincronía
  imagen/audio/subtítulos**.

### 2.9 Undo/redo
- Deshacer/rehacer sobre la tabla `Version`.

---

## 3. Iteraciones futuras (lo que NO entra en el MVP)

Se diseñan "enchufables" para no requerir reescritura, pero **no** se implementan en el
slice inicial:

- **Transiciones avanzadas** entre escenas más allá de fade/corte básico (wipes, zoom,
  morphs) y librería de transiciones de `@remotion/transitions` completa.
- **Efectos visuales** y filtros (color grading, blur, overlays), biblioteca de efectos.
- **Movimientos de cámara / keyframes** (Ken Burns parametrizado, paths, easing por
  keyframe). El MVP deja el campo `effects`/`camera` en el modelo pero sin editor de
  keyframes.
- **Reencuadre fino** (crop interactivo con *handles*, pan&zoom animado).
- **Curvas de volumen** (automatización/envelopes) más allá de gain + fades simples.
- **Generar/añadir recursos nuevos desde el editor** (llamar a los motores de imagen/voz/
  música dentro del editor). El MVP edita lo ya producido; la generación desde el editor es
  una extensión natural vía el registry existente.
- **Ducking automático** de música bajo la voz, beat-sync, waveforms de audio en la pista.
- **Exportar formatos/resoluciones alternativos** y presets de plataforma.
- **Colaboración/paridad total de operaciones en cliente** y edición offline.

---

## 4. Qué se verifica en el sandbox vs. en el PC del usuario

| Aspecto | Sandbox (aquí) | PC del usuario |
|--------|-----------------|----------------|
| Lógica pura (operations/convert/compile) | ✅ `pytest` determinista, sin render | ✅ |
| Mezcla de audio (AudioMixer, −14 LUFS) + mux | ✅ ffmpeg 7.0.2 disponible | ✅ |
| Export MP4 completo con Remotion | ⚠️ Chromium headless disponible (comprobado en features previas) → se intenta; si falla, el test se **salta con razón** | ✅ |
| `ffprobe` sobre MP4 real (streams + duración) | ✅ cuando el render corre | ✅ |
| Frontend build/lint/tsc + Remotion tsc | ✅ Node 22 | ✅ |
| Preview con Remotion Player (interacción real) | ⚠️ corre en navegador; se valida con build/typecheck y smoke documentado | ✅ verificación visual completa |
| Clips 3D (Blender) | ❌ Blender no instalado (no bloquea el editor: trabaja sobre clips ya renderizados) | ✅ |

Restricciones de entorno (del repo): Python 3.11.15 en `backend/.venv`, Node v22.23.4,
FFmpeg 7.0.2 en `/projects/sandbox/.localbin` (fuera del PATH, vía `AIV_FFMPEG_BIN`/
`AIV_FFPROBE_BIN`). `$HOME` y el gestor de paquetes del sistema son de solo lectura:
instalar dependencias en `backend/.venv` y `frontend/node_modules`.

---

## 5. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|-----------|
| **Media no servible por HTTP para el Player** | El preview no carga imágenes/audio/vídeo. | Usar `GET /assets/{id}/download` (ya existe, soporta `Range` para seeking) como fuente del Player; opción B: montar `StaticFiles` sobre `AIV_MEDIA_ROOT`. Verificar range requests en el smoke. |
| **Divergencia preview vs. export en audio** | "Lo que ves ≠ lo que exportas". | Mismo `EditProject` y mismos tiempos/gains/recortes en ambos; la única diferencia (loudness −14 LUFS en export) se documenta como mejora, no como cambio de contenido. |
| **Doble audio en export** (Player con `<Audio>` + AudioMixer) | Audio duplicado/fuera de fase. | En el render headless de export, `EditComposition` se renderiza **muda** (flag); el audio lo compone solo el AudioMixer. |
| **Remotion render no disponible en sandbox** | No se puede verificar el MP4 completo aquí. | Tests de export se **saltan con razón** (patrón `test_render_engine.py`); la lógica pura y el eslabón mux+mixer sí se verifican siempre. |
| **Refactor de `RenderEngine` rompe la generación** | Regresión en `generate_video`/`render`. | Añadir un método hermano `compose_edit` que comparte `_run_remotion`/`_mux`/`AudioMixer` sin tocar `compose`. La suite 145/2 debe seguir verde. |
| **Compartir el componente de composición entre `remotion/` y `@remotion/player` del frontend** | Duplicación/deriva. | MVP: componente mínimo compartido por copia tipada; unificación posterior. `EditComposition` registrada en `Root.tsx` es la fuente para el export. |
| **Crecimiento del `EditProject` / rendimiento del preview** | Player lento con muchos clips. | Modelo plano por clips con tiempos absolutos; memoización en el frontend; límites razonables en el MVP. |
| **Seeking/range en `FileResponse`** | `<OffthreadVideo>`/`<Audio>` no buscan bien. | Starlette `FileResponse` soporta `Range`; verificar en smoke; fallback a `StaticFiles`. |
| **Licencia Remotion (uso comercial)** | Restricción futura (ya anotada en `docs/RISKS-ASSUMPTIONS.md`). | Sin cambio respecto a hoy; el editor no agrava la situación. Documentado. |

---

## 6. Definición de "hecho" del MVP del editor

1. Desde un proyecto con vídeo generado, el botón «Editar vídeo» abre el editor con el
   `EditProject` derivado del timeline, con sus pistas pobladas.
2. El usuario puede recortar/dividir/eliminar/duplicar/reordenar/ajustar duración de clips;
   ajustar volumen y reposicionar audio; editar texto/tiempos/estilo de subtítulos;
   reemplazar/escalar/reposicionar imágenes o vídeos. Cada operación **persiste** y se ve
   en el preview.
3. El preview (Remotion Player) reproduce el proyecto editado **con audio** sincronizado.
4. Undo/redo funciona sobre `Version`.
5. La exportación re-renderiza desde el `EditProject` y produce un MP4 descargable con la
   sincronía imagen/audio/subtítulos conservada (verificado con `ffprobe` donde el render
   está disponible; lógica verificada con `pytest` puro siempre).
6. La suite existente (145 passed / 2 skipped con ffmpeg) sigue verde; se añaden tests
   nuevos del editor. Frontend y Remotion pasan build/lint/typecheck.
