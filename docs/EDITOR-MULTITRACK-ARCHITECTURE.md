# Editor multipista (tipo CapCut) — Arquitectura

> Estado: **PLAN / diseño**. Este documento describe cómo se incorpora un módulo de
> edición de vídeo profesional e integrado a la plataforma `audiovisual-ia` sin romper el
> pipeline de generación existente ni la arquitectura modular por motores. La
> implementación se hace tarea por tarea desde `.agents/tasks/task-editor-multipista/`.

Documento hermano: [`EDITOR-MVP-SCOPE.md`](EDITOR-MVP-SCOPE.md) (alcance del MVP del
editor frente a iteraciones futuras, riesgos y qué se verifica en el sandbox vs. en el PC
del usuario).

---

## 1. Objetivo y principio rector

Tras la generación/composición inicial del vídeo, el usuario abre un **editor dedicado**
(no una pantalla de parámetros) donde puede:

- Organizar el contenido en una **línea de tiempo MULTIPISTA** (vídeo/imagen, voz, música,
  SFX, subtítulos como pistas independientes).
- Editar **clips**: recortar (trim), dividir (split), eliminar, duplicar, reordenar,
  ajustar duración.
- Editar **audio**: volumen por clip, recortar, reposicionar voces, música de fondo, SFX.
- Editar **subtítulos**: texto, tipografía, tamaño, color, posición, estilo, tiempos.
- Añadir **transiciones y efectos** entre escenas, animaciones, movimientos de cámara.
- Editar **imágenes/vídeos**: reemplazar recurso, tamaño, posición, encuadre, duración.
- **Previsualizar en tiempo real** el proyecto editado.
- **Exportar** el vídeo final conservando la sincronización imagen/audio/subtítulos.

**Principio rector: "lo que ves es lo que exportas".** El preview (Remotion Player en el
navegador) y la exportación (Remotion headless + FFmpeg) consumen la **misma composición
Remotion** alimentada por el **mismo estado de edición** (`EditProject`). Cada operación es
REAL: muta el estado de edición persistido en el backend y se refleja tanto en el preview
como en el export. Nada cosmético.

Invariantes de la plataforma que este módulo respeta (prompt maestro §8/§9 y README):
- No se reescribe el núcleo ni se eliminan módulos. Se **añade** un motor de edición.
- Proveedores conmutables intactos: la vía de pago (OpenAI/ElevenLabs/Stability/
  veo/runway/kling) sigue siendo un camino futuro conmutable; el editor no la toca.
- 2D y 3D siguen funcionando: el editor opera sobre `Asset`/`Timeline` ya producidos,
  sea cual sea el motor que los generó.
- Nada simulado presentado como real: una operación que no se puede aplicar (p. ej.
  export sin Chromium) falla con un mensaje accionable, no finge un resultado.

---

## 2. Qué existe hoy (base sobre la que se construye)

Resumen de la arquitectura ya consolidada en `main`, con las rutas reales:

| Pieza | Ruta | Rol actual |
|-------|------|-----------|
| `Timeline` (ORM) | `backend/app/models/entities.py` | JSON serializado versionado que consume Remotion (`id, project_id, json, version`). |
| `Version` (ORM) | `backend/app/models/entities.py` | Historial por entidad editable (`entity_type, entity_id, version, snapshot`) — **pensada para undo/redo**. |
| `Asset` (ORM) | `backend/app/models/entities.py` | Media con `kind ∈ {image, voice, music, sfx, video, srt}`, opcionalmente ligado a `scene_id`. |
| Esquema timeline | `backend/app/engines/render/timeline.py` | Pydantic `Timeline / TimelineScene / SubtitleCue` + `TimelineBuilder`. Hoy es **por escena**, no multipista. |
| `RenderEngine` | `backend/app/engines/render/engine.py` | `compose()`: persiste timeline → Remotion (vídeo mudo) → `AudioMixer` → mux FFmpeg → MP4 + `Asset(kind='video')`. |
| `AudioMixer` | `backend/app/engines/voice/mixer.py` | Mezcla voz/música/SFX con `MixTrack(path, gain, delay_s)`, `amix` + `loudnorm` 2 pasadas a −14 LUFS. |
| Composición Remotion | `remotion/src/{Root,TimelineComposition,types}.tsx` | Dibuja por escena `<Img>`/`<OffthreadVideo>` + subtítulos con `<Sequence>`. **No compone audio** (lo muxea FFmpeg). |
| Script de render | `remotion/scripts/render.mjs` | `bundle` + `selectComposition("Timeline")` + `renderMedia`, timeline como `inputProps`, `publicDir = AIV_MEDIA_ROOT`. |
| API render | `backend/app/api/render.py` | `POST /projects/{id}/render` (encola job), `GET .../render` (estado), `GET .../video` (MP4). |
| Cola/worker | `backend/app/jobs/{queue,worker,handlers,pipeline}.py` | Jobs respaldados por BD con `progress` y `result`; handler `render` y `generate_video`. |
| API assets | `backend/app/api/assets.py` | `GET /projects/{id}/assets`, `GET /assets/{id}/download` (FileResponse). |
| Frontend | `frontend/components/{RenderPanel,ProjectWorkspace,...}.tsx`, `frontend/lib/api.ts` | Cliente REST tipado; `RenderPanel` encola render y previsualiza/descarga el MP4. |

### 2.1 Hallazgos relevantes para el diseño

1. **El timeline es por escena, no multipista.** `TimelineScene` tiene UN `image_src`/
   `video_src`, UN `audio_src` y una lista de `cues`. Las pistas (voz/música/SFX) no
   viven en el timeline: el `RenderEngine._audio_tracks` las deriva de los `Asset` del
   proyecto en tiempo de render y las secuencia por offset de escena. El editor necesita
   un modelo **explícitamente multipista** que capture posiciones, recortes, volúmenes y
   estilos editados, y que se pueda **compilar** de vuelta a lo que Remotion+FFmpeg saben
   consumir.

2. **El audio NO está en Remotion hoy.** Esto es un problema para el preview: el Player
   del navegador debe **oír** el audio sincronizado. La solución (sección 6) es componer
   el audio por pista con `<Audio>`/`<Sequence>` dentro de la composición Remotion para el
   PREVIEW, manteniendo a la vez la ruta de export actual (AudioMixer+FFmpeg) para la
   calidad de masterización a −14 LUFS. Se documenta cómo ambas rutas parten del mismo
   estado de edición.

3. **No hay montaje de archivos estáticos HTTP para la media.** El backend sirve media
   solo vía `GET /assets/{id}/download` (por asset). Remotion headless lee de disco con
   `publicDir`. Pero el **Remotion Player en el navegador** necesita URLs HTTP para cada
   recurso (imagen/vídeo/audio). Hay que **servir el workspace de media por HTTP**
   (montaje `StaticFiles` o endpoint de media por ruta relativa) para el preview. Es una
   pieza nueva y necesaria (ver sección 6.2).

4. **`Version` ya está modelada para undo/redo** (`entity_type='edit_project'`,
   `entity_id=project_id`, `snapshot=JSON`). El editor la usa directamente.

5. **Reutilización directa de piezas:** cola/worker de jobs (progreso), `AudioMixer`
   (ahora con `gain` y `delay_s` por pista — justo lo que el editor necesita), patrón de
   persistencia de `Asset`, patrón de verificación con `ffprobe` de `test_render_engine.py`.

---

## 3. Modelo de datos de edición (`EditProject`)

El editor introduce un **modelo de proyecto de edición multipista** independiente del
`Timeline` por escena. Es un documento JSON versionado, validado con Pydantic, que vive en
la tabla `Timeline` reutilizada (ver §3.4) y se historiza en `Version` para undo/redo.

### 3.1 Jerarquía

```
EditProject
  ├─ meta: fps, width, height, aspect_ratio, duration_s (derivada)
  ├─ tracks: [ EditTrack, ... ]            # pistas independientes, ordenadas por z/orden
  │     ├─ id, kind, name, order, muted, locked, volume (pista)
  │     └─ clips: [ EditClip, ... ]        # clips posicionados en el tiempo
  └─ source_timeline_version: int          # Timeline del que se derivó (trazabilidad)

EditTrack.kind ∈ { "video", "image", "voice", "music", "sfx", "subtitle" }

EditClip (campos comunes):
  id, track_id, start_s, duration_s        # posición y duración en la línea de tiempo
  source_in_s, source_out_s                # recorte dentro del recurso origen (trim/split)
  asset_id | null                          # recurso de media (Asset) cuando aplica
  z_order, enabled

EditClip extensiones por tipo de pista:
  visual (video/image):
    transform: { scale, x, y, rotation, crop:{l,t,r,b}, fit:"cover"|"contain" }
    transition_in / transition_out: { type:"none"|"fade"|"slide"|..., duration_s }
    effects: [ { type, params } ]          # animaciones / movimientos de cámara (Ken Burns)
  audio (voice/music/sfx):
    gain                                    # volumen del clip (multiplica el de la pista)
    fade_in_s, fade_out_s
  subtitle:
    text
    style: { font_family, font_size, color, bg_color, bold, outline,
             position:"top"|"center"|"bottom", x, y, preset:"classic"|"tiktok" }
```

Modelo Pydantic propuesto en `backend/app/engines/editor/model.py` (nuevo motor "editor",
hermano de `render`). Todo clip tiene tiempos **absolutos en la línea de tiempo**
(`start_s`, `duration_s`) y, cuando referencia un recurso recortable, el sub-rango del
origen (`source_in_s`, `source_out_s`). Esto hace que split/trim/reposición sean
operaciones puras sobre números, testeables sin render.

### 3.2 Operaciones de edición (lógica PURA)

Todas se implementan como funciones puras `op(edit_project, params) -> edit_project` en
`backend/app/engines/editor/operations.py`, sin tocar disco ni render. Esto permite
tests `pytest` deterministas y rápidos (sin ffmpeg/Chromium):

- **Clips (escenas):** `trim` (ajusta `source_in/out` y `duration_s`), `split` (parte un
  clip en dos en un instante `t`), `delete`, `duplicate`, `reorder`/`move`
  (cambia `start_s`/pista), `set_duration`.
- **Audio:** `set_clip_gain`, `set_track_volume`, `trim` (igual que clips),
  `move`/reposicionar voces, `set_fade`.
- **Subtítulos:** `set_text`, `set_style` (font/size/color/position/preset), `set_timing`
  (`start_s`/`duration_s`), `split`/`merge`.
- **Transiciones/efectos (MVP acotado, ver scope):** `set_transition`, `add_effect`,
  `remove_effect`.
- **Imágenes/vídeos:** `replace_asset` (cambia `asset_id`), `set_transform`
  (scale/x/y/crop/fit), `set_duration`.

Cada operación valida invariantes (no duraciones negativas, no solapes inválidos dentro de
una pista donde no se permiten, tiempos dentro del rango del origen) y devuelve un nuevo
`EditProject` (inmutable) listo para persistir + versionar.

### 3.3 Conversión timeline por escena ↔ EditProject

**`Timeline` (por escena) → `EditProject` (multipista)** — al abrir el editor:

- Por cada `TimelineScene` en orden se crea, con el offset acumulado como `start_s`:
  - un `EditClip` en la pista **video** (si `video_src`) o **image** (si `image_src`),
    con `duration_s` = duración de la escena, `transform` por defecto (fit cover),
    `asset_id` resuelto al `Asset` correspondiente.
  - si la escena tiene voz (`Asset(kind='voice')` de esa escena), un `EditClip` en la
    pista **voice** con `start_s` = offset de la escena y `duration_s` = duración real de
    la voz (de `Asset.meta['duration_s']`), `gain=1.0`.
  - por cada `SubtitleCue` de la escena, un `EditClip` en la pista **subtitle** con
    `start_s` = offset_escena + `cue.start_s`, `duration_s` = `cue.end_s − cue.start_s`,
    `text` = `cue.text`, `style.preset` derivado de `subtitle_style`.
- Música/SFX a nivel de proyecto (`Asset(kind='music'|'sfx')`) se añaden como clips en sus
  pistas desde `start_s=0` (fondo), con el `gain` por defecto que hoy usa el mixer
  (música 0.3, SFX 0.5) para preservar el comportamiento actual como punto de partida.

Esta conversión vive en `backend/app/engines/editor/convert.py::timeline_to_edit_project`.
Es determinista y testeable: dado un `Timeline`, produce un `EditProject` equivalente cuyo
render debe coincidir con el render del timeline original (invariante de regresión).

**`EditProject` → artefactos de export** — al exportar (ver §7):

- **Composición Remotion:** se compila el `EditProject` a un `inputProps` multipista
  (`EditComposition`, §6.1): lista de pistas visuales + pista de subtítulos con estilos.
- **Mezcla de audio:** las pistas de audio (voice/music/sfx) se compilan a una lista de
  `MixTrack(path, gain, delay_s)` que consume el `AudioMixer` **ya existente**: cada clip
  de audio aporta un `MixTrack` con `delay_s = start_s` y `gain` = volumen efectivo
  (pista × clip). El recorte (`source_in/out`) y fades se aplican pre-mezcla con un filtro
  FFmpeg `atrim`/`afade` por clip (extensión menor del mixer o paso de preparación).

Función `backend/app/engines/editor/compile.py` con dos salidas:
`compile_remotion_props(edit_project)` y `compile_audio_tracks(edit_project)`.

### 3.4 Persistencia: reutilizar `Timeline` + `Version`

- El `EditProject` se guarda como una fila de la tabla **`Timeline`** existente, usando su
  campo `json` para el documento de edición y `version` para el versionado incremental. Se
  distingue del timeline de generación por una marca en el JSON (`"kind": "edit_project"`)
  y/o por ser la versión activa que el editor gestiona. **No se añade tabla nueva** (el
  modelo de datos ya previó un timeline JSON versionado por proyecto).
  - *Alternativa considerada:* columna/tabla dedicada. Se descarta para el MVP porque
    `Timeline(json, version)` ya cubre exactamente "documento JSON versionado por
    proyecto" y evita una migración de esquema. Si en el futuro conviene separarlos, el
    `EditProject` es autocontenido y migrable.
- **Undo/redo** usa la tabla **`Version`** con `entity_type='edit_project'`,
  `entity_id=project_id`, `version` incremental y `snapshot` = JSON del `EditProject` tras
  cada operación (o lote de operaciones). Undo = cargar el snapshot anterior; redo =
  avanzar. Un puntero de versión activa (campo en el JSON del `EditProject` o la fila
  `Timeline` de mayor versión marcada activa) indica dónde está el usuario en el historial.

---

## 4. API de edición (REST)

Nuevo router `backend/app/api/editor.py` (registrado en `backend/app/main.py` junto a los
demás). Todas las rutas cuelgan del proyecto y devuelven/aceptan el `EditProject` validado.

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/projects/{id}/editor/open` | Abre el editor: si no hay `EditProject`, lo deriva del último `Timeline` del proyecto (`timeline_to_edit_project`) y lo persiste; si existe, devuelve el activo. Idempotente. |
| `GET` | `/projects/{id}/editor` | Devuelve el `EditProject` activo (estado completo para el frontend). |
| `PUT` | `/projects/{id}/editor` | Reemplaza el `EditProject` completo (guardado directo del estado editado en el cliente). Crea una nueva `Version`. |
| `POST` | `/projects/{id}/editor/op` | Aplica UNA operación (`{op, params}`) en el backend con la lógica pura, persiste y crea `Version`. Devuelve el `EditProject` resultante. (Camino "servidor autoritativo" para operaciones críticas / validadas.) |
| `POST` | `/projects/{id}/editor/undo` | Carga el snapshot de `Version` anterior y lo hace activo. |
| `POST` | `/projects/{id}/editor/redo` | Avanza al snapshot siguiente. |
| `GET` | `/projects/{id}/editor/history` | Lista las versiones (para una UI de historial). |
| `POST` | `/projects/{id}/editor/export` | Encola un job `edit_export` que re-renderiza desde el `EditProject` (ver §7). Devuelve el job para polling. |
| `GET` | `/projects/{id}/editor/export` | Estado del último export de edición. |

Notas:
- `PUT` (estado completo) y `POST /op` (operación puntual) coexisten a propósito: el
  frontend puede aplicar operaciones localmente para respuesta inmediata y persistir el
  estado completo con `PUT` (con versionado), o delegar operaciones críticas al backend
  con `/op`. La **lógica pura es la misma** en ambos lados conceptualmente; la fuente de
  verdad es el backend. En el MVP se implementa `PUT` + `/op` para las operaciones del
  slice; la paridad de operaciones en cliente es progresiva.
- El descargable final reutiliza `GET /projects/{id}/video` (el export de edición produce
  un `Asset(kind='video')` nuevo, que pasa a ser el más reciente).

Esquemas Pydantic de request/response en `backend/app/models/schemas.py` (p. ej.
`EditProjectRead`, `EditOperationRequest`, `EditExportResponse`).

---

## 5. Esquema del EditProject (contrato frontend ↔ backend)

El `EditProject` es el contrato. El frontend (TypeScript) replica el esquema Pydantic en
`frontend/lib/editor/types.ts` (espejo, como ya se hace con `frontend/lib/api.ts`). La
MISMA forma alimenta el Remotion Player (preview) y el job de export (vía el compilador del
backend). Resolución de rutas de media:

- En el `EditProject` los recursos se referencian por `asset_id`. Para el **preview** el
  frontend resuelve cada `asset_id` a una URL HTTP servible (ver §6.2). Para el **export**
  el backend resuelve `asset_id` a la ruta de disco relativa a `AIV_MEDIA_ROOT` (como hoy)
  para Remotion headless y a la ruta absoluta para FFmpeg.

---

## 6. Vista previa en tiempo real (Remotion Player)

### 6.1 Composición compartida preview/export: `EditComposition`

Se añade una composición Remotion **multipista** nueva, `EditComposition`
(`remotion/src/EditComposition.tsx`), registrada en `Root.tsx` junto a `Timeline`
(la actual **se conserva** intacta para la generación). `EditComposition`:

- Recibe como `inputProps` el resultado de `compile_remotion_props(edit_project)`:
  pistas visuales (clips con `start`, `duration`, `asset url/src`, `transform`,
  `transition`, `effects`) y pista de subtítulos (clips con `text` + `style`).
- Dibuja cada clip visual con `<Sequence from=... durationInFrames=...>` conteniendo
  `<Img>`/`<OffthreadVideo>` con el `transform` aplicado (scale/pos/crop/fit) y las
  transiciones (fade/slide) via `@remotion/transitions` o interpolación simple.
- Dibuja los subtítulos con su estilo (reutilizando y generalizando los overlays
  `ClassicSubtitle`/`TikTokSubtitle` ya existentes en `TimelineComposition.tsx`, ahora
  parametrizados por `style`).
- **Compone el AUDIO para el preview** con `<Audio src=... />` dentro de `<Sequence>`
  posicionado en `start_s` y con `volume` = gain efectivo, por cada clip de voz/música/SFX.
  Así el Player **suena sincronizado**. (En export, el audio de alta calidad lo sigue
  produciendo el AudioMixer a −14 LUFS; ver §7 y §6.3.)

La **misma** `EditComposition` se usa tanto en el Player del navegador como, opcionalmente,
en el render headless (ver §7 para la decisión export).

El Player se integra en el frontend con `@remotion/player` (`<Player>`), en una nueva ruta
`frontend/app/projects/[id]/editor/page.tsx` → componente `EditorWorkspace`. El Player
recibe `component={EditComposition}` e `inputProps` derivados del `EditProject` en el
estado de React: **cada edición re-renderiza el Player al instante** (preview en tiempo
real, sin ir al backend para verse).

### 6.2 Servir media por HTTP para el Player (pieza nueva necesaria)

El Player corre en el navegador y necesita URLs HTTP para imágenes/vídeos/audios. Hoy no
existe. Se añade una de estas dos opciones (el plan recomienda la primera por simplicidad):

- **Opción A (recomendada):** reutilizar `GET /assets/{id}/download` como fuente del
  recurso en el Player (ya devuelve `FileResponse` con el fichero). El frontend construye
  `assetDownloadUrl(assetId)` (ya existe en `frontend/lib/api.ts`). Suficiente para
  imágenes, audio y vídeo de escena.
- **Opción B:** montar `StaticFiles` sobre `AIV_MEDIA_ROOT` en `backend/app/main.py`
  (p. ej. `/media/...`) para servir por ruta relativa, alineado con el `publicDir` de
  Remotion. Útil si se quieren URLs idénticas a las del render.

El MVP usa la Opción A (menos superficie, ya probada). Nota: `<OffthreadVideo>` y
`<Audio>` del Player requieren que el servidor soporte *range requests* para el *seeking*;
`FileResponse` de Starlette soporta `Range`. Verificar en el smoke del preview.

### 6.3 "Lo que ves es lo que exportas"

El preview y el export parten del **mismo `EditProject`**. Diferencias controladas y
documentadas:

- **Vídeo + subtítulos + transforms + transiciones:** idénticos (misma `EditComposition`).
- **Audio:** el preview lo reproduce el Player (mezcla del navegador, sin masterizar). El
  export lo masteriza el `AudioMixer` a −14 LUFS. La **posición, recorte, volumen relativo
  y selección de pistas** son los mismos (derivados del mismo `EditProject`); lo que cambia
  es la normalización de loudness final (una mejora, no una divergencia de contenido). Esto
  se documenta explícitamente al usuario como nota del preview.

---

## 7. Exportación: re-render desde el estado editado

Nuevo tipo de job **`edit_export`** (handler en `backend/app/jobs/handlers.py`, registrado
como los existentes) que reutiliza la cola/worker y el `RenderEngine`. Flujo:

1. Cargar el `EditProject` activo del proyecto.
2. `compile_remotion_props(edit_project)` → escribir `edit_timeline.json` en el workspace
   (rutas de imagen/vídeo relativas a `AIV_MEDIA_ROOT`, como hoy).
3. Render del **vídeo mudo** con Remotion usando la composición `EditComposition`
   (extender `remotion/scripts/render.mjs` para aceptar `--composition Edit` o un segundo
   script `render-edit.mjs`; `selectComposition` por id). Reutiliza el mismo mecanismo de
   `RenderEngine._run_remotion` (mismo Node/bundler/`RenderUnavailable`).
4. `compile_audio_tracks(edit_project)` → lista de `MixTrack(path, gain, delay_s)` (con
   recorte/fades pre-aplicados por clip). Mezclar con el `AudioMixer` existente a −14 LUFS.
5. Mux vídeo mudo + mezcla con FFmpeg (`RenderEngine._mux`, ya existente), produciendo el
   MP4 final y un `Asset(kind='video')` nuevo (el más reciente → descargable por
   `GET /projects/{id}/video`).

Decisión de diseño sobre el audio en export: **se mantiene el AudioMixer + mux** (no se
delega el audio a Remotion) para preservar la masterización a −14 LUFS ya validada
(FEAT-005/FIX-A) y el comportamiento probado. La composición `EditComposition` puede llevar
`<Audio>` para el preview; en el render headless de export se puede renderizar **sin audio**
(vídeo mudo) y dejar que el AudioMixer lo componga, evitando doble audio. Se parametriza
(`muted`/flag) para que el mismo componente sirva a ambos.

Reutilización explícita: `RenderEngine.compose` se **refactoriza mínimamente** para admitir
una fuente de timeline inyectable (el `EditProject` compilado) y una composición objetivo,
sin romper la ruta actual `generate_video`/`render` (que sigue usando `TimelineBuilder` +
composición `Timeline`). Alternativamente se añade un método hermano
`RenderEngine.compose_edit(edit_project, ...)` que comparte `_run_remotion`, `_mux` y el
`AudioMixer`. El plan prefiere el **método hermano** para no tocar la ruta probada.

---

## 8. Frontend: botón «Editar vídeo» y espacio de edición

- **Botón «Editar vídeo»:** visible tras la generación/composición (cuando hay un MP4/
  timeline), en `RenderPanel.tsx` (junto a "Descargar MP4") y/o `ProjectWorkspace.tsx`.
  Navega a `/projects/[id]/editor`.
- **Ruta nueva:** `frontend/app/projects/[id]/editor/page.tsx` → `EditorWorkspace.tsx`:
  - Carga el `EditProject` (`POST .../editor/open` + `GET .../editor`).
  - **Player** (`@remotion/player`) con `EditComposition` e `inputProps` del estado.
  - **Panel de línea de tiempo multipista**: pistas (vídeo/imagen/voz/música/SFX/
    subtítulos) con sus clips; interacción para seleccionar, recortar, dividir, mover,
    duplicar, eliminar, ajustar duración.
  - **Inspector** contextual del clip seleccionado: volumen/fades (audio), texto/estilo/
    tiempos (subtítulo), transform/transición/duración (visual), reemplazo de recurso.
  - **Undo/redo** (botones) → `POST .../editor/undo|redo`.
  - **Exportar** → `POST .../editor/export` + polling (reutiliza `JobProgress` y
    `useJobPolling`) + descarga vía `videoDownloadUrl`.
- **Cliente API:** nuevas funciones tipadas en `frontend/lib/api.ts` (open/get/put/op/
  undo/redo/history/export) y tipos del `EditProject` en `frontend/lib/editor/types.ts`.
- **Dependencia nueva:** `@remotion/player` (misma major `^4.0.0` que el resto de Remotion)
  en `frontend/package.json`. El código de la composición puede compartirse con
  `remotion/src` vía copia tipada o import relativo (decisión de implementación; el MVP
  puede duplicar el componente mínimo y unificarlo en una iteración si hace falta).

---

## 9. Qué se conserva y por qué NO se rompe nada

- **Generación (prompt→vídeo):** el pipeline `generate_video` y el `TimelineBuilder` no se
  tocan. El editor **consume** su salida (`Timeline`) al abrirse. Si el usuario no edita,
  el render de generación sigue igual.
- **Composición Remotion `Timeline`:** se conserva; se **añade** `EditComposition`. El
  render de generación sigue usando `Timeline`.
- **AudioMixer / mux / −14 LUFS:** se reutilizan tal cual; el editor solo cambia la lista
  de `MixTrack` (gains/delays/recortes) que se le pasa.
- **Cola/worker/jobs:** se añade el tipo `edit_export`; los demás jobs no cambian.
- **2D y 3D:** el editor opera sobre `Asset`/`Timeline` producidos por cualquier motor; no
  distingue el origen salvo el `subtitle_style` por defecto (que ahora es editable por
  clip). Blender no se necesita en el editor (trabaja sobre clips ya renderizados).
- **Vía de pago conmutable:** intacta. El editor no resuelve proveedores de IA; edita media
  ya producida. La generación de nuevos recursos desde el editor (si se añadiera) usaría el
  registry existente, respetando la conmutación.
- **Modelo de datos:** se reutilizan `Timeline` y `Version`; no se elimina ni renombra
  nada. Posible añadido menor: índice/campo auxiliar si hiciera falta marcar la versión
  activa (evaluar en implementación; preferible marca dentro del JSON para evitar
  migración).

---

## 10. Estrategia de pruebas (alineada con el sandbox)

- **Lógica pura (sin render):** `operations.py`, `convert.py`, `compile.py` se testean con
  `pytest` de forma determinista: split/trim/reorder/duración/volumen/subtítulos producen
  el `EditProject` esperado; la conversión timeline↔EditProject es ida y vuelta coherente;
  el compilador produce `MixTrack` e `inputProps` correctos. **Sin ffmpeg ni Chromium.**
- **Export re-render (con ffprobe, se salta con razón):** como `test_render_engine.py`, un
  test de integración construye un `EditProject` mínimo con imagen placeholder real + voz
  WAV real, ejecuta `compose_edit` y verifica con `ffprobe` que el MP4 tiene stream de
  vídeo + audio y duración coherente; si Remotion no puede renderizar (sin Chromium/
  glibc), el test se **salta con la razón** (nunca simula el MP4). El eslabón mux+AudioMixer
  sí se ejercita siempre que haya ffmpeg (disponible en el sandbox).
- **Frontend/preview:** `npm run build`, `npm run lint`, `npm run typecheck` (tsc) del
  frontend y de remotion; el Player se valida con build/typecheck y, si es viable, un smoke
  manual documentado (el Player corre en navegador; la verificación automatizada completa
  del preview queda para el PC del usuario).
- **Sincronía imagen/audio/subtítulos:** se verifica a nivel de compilador (los `start_s`/
  `duration_s`/`delay_s` coinciden entre pista visual, pista de audio y cues) con tests
  puros, y a nivel de MP4 con `ffprobe` (duración total y presencia de streams) cuando el
  render está disponible.

Comandos de referencia (del repo):
- Backend: `cd backend && . .venv/bin/activate && AIV_FFMPEG_BIN=/projects/sandbox/.localbin/ffmpeg AIV_FFPROBE_BIN=/projects/sandbox/.localbin/ffprobe pytest -q`
- Frontend: `cd frontend && npm run build && npm run lint && npm run typecheck`
- Remotion: `cd remotion && npm run typecheck`

---

## 11. Diagrama de flujo (resumen)

```
Generación (existente)                 Edición (nuevo)
prompt → ... → TimelineBuilder         open: Timeline → timeline_to_edit_project → EditProject
            → Timeline (por escena)                       │ (persistido en tabla Timeline, versionado en Version)
            → RenderEngine.compose                        ▼
            → MP4 (Asset video)         Frontend EditorWorkspace
                                          ├─ Remotion Player (EditComposition + inputProps)  ← preview en tiempo real (con audio)
                                          ├─ Timeline multipista + Inspector  → operations (puras) → PUT/op → Version (undo/redo)
                                          └─ Export → job edit_export:
                                               compile_remotion_props → render.mjs (EditComposition, mudo)
                                               compile_audio_tracks  → AudioMixer (−14 LUFS)
                                               _mux (FFmpeg)         → MP4 final (Asset video) → GET /video
```
