# Bloque 4 — Administración de proyectos y Control de Calidad

Documento de diseño del último bloque del roadmap (`docs/EDITOR-ROADMAP.md` §BLOQUE 4).
Se construye SOBRE lo ya consolidado en `main` (MVP 2D, Motor 3D Blender, Editor
Multipista, Subtítulos/Títulos Pro, Editor Avanzado, Motor ComfyUI) reutilizando la
arquitectura existente (`docs/ARCHITECTURE.md`). Todo real y conectado al backend, con
pruebas; nada decorativo ni simulado.

Alcance de este bloque:

1. Administración de proyectos (enriquecer la base ya existente).
2. Control de calidad (QA report por render, con checks reales vía ffprobe/ffmpeg).
3. Herramientas de edición inteligente honestas (auto-corte por silencios + sugerencias
   deterministas).
4. Nota honesta sobre producción cinematográfica (qué hay en 2D/3D, qué se difiere).

Fuera de alcance (Fase 4 comercial, no se planifica aquí): usuarios/login/multiusuario y
planes de suscripción. La app sigue monousuario local.

---

## 0. Estudio de lo que YA existe (base a reutilizar, no rehacer)

### 0.1 Modelo de datos (`backend/app/models/entities.py`)

- `Project`: `id`, `name`, `prompt`, `production_type`, `aspect_ratio`, `resolution`,
  `status`, `provider_overrides` (JSON), `created_at`. Relaciones a `scripts`, `scenes`,
  `assets`, `timelines`, `jobs`, todas con `cascade="all, delete-orphan"`.
- `Scene`: `project_id` (FK `ondelete="CASCADE"`), `order`, `narration_text`,
  `visual_prompt`, `duration_s`, `camera_notes`, `status`; relación `assets` con cascada.
- `Asset`: `project_id`, `scene_id` (nullable, FK `ondelete="CASCADE"`), `kind`
  (`image|voice|music|sfx|video|srt`), `path`, `provider`, `meta` (JSON), `created_at`.
- `Timeline`: `project_id`, `json` (timeline serializado que consume Remotion), `version`.
  Nota clave: el editor multipista PERSISTE su `EditProject` reutilizando esta tabla
  (ver `app/engines/editor/store.py`), por secuencia.
- `Version`: historial por entidad editable (`entity_type`, `entity_id`, `version`,
  `snapshot`) — ya soporta undo/redo del editor.
- `Job`: cola respaldada por BD (`type`, `status`, `progress`, `error`, `retries`,
  `max_retries`, `payload`, `result`).

Consecuencia para duplicar/borrar: la cascada ORM ya limpia filas hijas al borrar un
`Project`; lo que la BD NO sabe borrar es el **workspace de media en disco**, que hay que
limpiar explícitamente.

### 0.2 Workspace de media (`backend/app/storage/media.py`)

- `media_root()` resuelve `AIV_MEDIA_ROOT` (crea si falta).
- `project_dir(project_id)` → `media_root()/project_{id}`.
- `ensure_project_workspace(project_id)` crea el árbol con subcarpetas estándar
  `PROJECT_SUBDIRS = ("images","voice","music","sfx","video","srt","timeline")`.
- Las subidas del editor (`overlays/`, `media/uploads/`) también cuelgan de ahí.

Para duplicar hay que copiar `project_{old}` → `project_{new}` y reescribir las rutas de
los `Asset` copiados. Para borrar hay que hacer `shutil.rmtree(project_dir(id))` de forma
segura (confirmar que la ruta está dentro de `media_root()` antes de borrar).

### 0.3 API actual (`backend/app/api/`)

- `projects.py`: `POST /projects`, `GET /projects`, `GET /projects/{id}`,
  `PATCH /projects/{id}` (ya permite renombrar vía `name`), `DELETE /projects/{id}`
  (hoy solo borra filas, NO limpia disco).
- `scenes.py`, `assets.py`, `jobs.py`, `render.py`, `generation.py`, `editor.py`,
  `settings.py`. Routers montados en `backend/app/main.py` con `app.include_router(...)`.
- Patrón de router: `APIRouter(prefix=..., tags=[...])`, helper `_get_or_404`,
  `Depends(get_db)`, respuestas Pydantic con `from_attributes`.

### 0.4 Medición ffprobe/ffmpeg ya presente (a reutilizar para QA)

- `engines/render/engine.py`: compone el MP4, persiste `Asset(kind='video')` con
  `meta={duration_s, width, height, fps, ...}`. Invoca ffmpeg/ffprobe vía
  `settings.ffmpeg_bin`/`settings.ffprobe_bin` (`AIV_FFMPEG_BIN`/`AIV_FFPROBE_BIN`).
- `engines/voice/master.py`: loudness con `ebur128` (`measure_integrated` →
  `parse_integrated_lufs`), objetivos `TARGET_I=-14`, `TARGET_TP=-1.2`. Dos pasadas
  de `loudnorm`.
- `engines/voice/mixer.py`: mezcla a −14 LUFS y mide la loudness integrada final.
- `engines/voice/trim.py`: `silencedetect` + `_probe_duration` (ffprobe
  `format=duration`) + re-mapeo de `WordTiming` (lógica pura testeable:
  `speech_segments`, `remap_timings`, `parse_silencedetect`).
- `storage/uploads.py`: `probe_media(path)` → `MediaProbe(duration_s, has_video,
  has_audio, width, height)` con ffprobe JSON (`-show_format -show_streams`). Patrón
  directo para leer duración/resolución del MP4 en el QA report.

### 0.5 Frontend (`frontend/`)

- `lib/api.ts`: cliente REST tipado; helper `request<T>` con `ApiError`; ya hay
  `listProjects/createProject/updateProject/deleteProject`, render y editor.
- `components/Dashboard.tsx`: lista proyectos (cards con `Link` a `/projects/{id}`) y
  formulario de creación. Aquí encajan las acciones de admin por proyecto, tags y
  búsqueda/filtrado.
- `components/RenderPanel.tsx`: encola render, hace polling del job y muestra/descarga
  el MP4. Aquí encaja el panel del QA report del render.
- `components/ui.tsx`: `Button`, `Card`, `ErrorBanner`, `Field`, `TextInput`, `Select`,
  `StatusBadge`, etc. (reutilizar, no crear design system nuevo).

### 0.6 Edición inteligente ya existente

- Auto-subtítulos (faster-whisper, por palabra) y auto-mezcla −14 LUFS: ya en `main`.
- `engines/voice/trim.py` (`SilenceTrimmer`): recorte por silencios con re-mapeo de
  timings. Hoy se activa por config en el pipeline (`AIV_TRIM_SILENCE`); NO está expuesto
  como acción puntual del editor/QA.
- Ken Burns / transforms por clip: ya existen en el modelo del editor
  (`engines/editor/model.py`: `VisualTransform`, `VisualEffect` "Ken Burns"; compilados a
  props de Remotion en `engines/editor/compile.py`).

---

## 1. Administración de proyectos

Objetivo: enriquecer la gestión sin romper el CRUD existente ni la vía de pago.

### 1.1 Duplicar proyecto (copia profunda + media)

Nuevo endpoint: `POST /projects/{id}/duplicate` (body opcional `{ "name": "..." }`).

Comportamiento:

1. Crea un `Project` nuevo copiando campos editoriales (`name` → `"<name> (copia)"` si no
   se pasa uno; `prompt`, `production_type`, `aspect_ratio`, `resolution`,
   `provider_overrides`; `status="draft"`). `created_at` nuevo.
2. `ensure_project_workspace(new.id)` y copia recursiva de los ficheros del workspace
   origen a los subdirectorios equivalentes del destino (`shutil.copy2`).
3. Copia profunda de filas hijas: `Scene` (conservando `order`), `Asset`, `Timeline`
   (incluido el/los `EditProject` persistidos como `Timeline`), `Script`. Las FKs
   `scene_id` de los `Asset` se re-mapean al `id` de la escena duplicada (diccionario
   `old_scene_id → new_scene_id`). El `Asset.path` se reescribe apuntando al fichero
   copiado en el nuevo workspace (y, si el path del timeline/EditProject embebe rutas de
   recurso, se reescriben con el mismo re-mapeo de ruta base `project_{old}` →
   `project_{new}`).
4. NO copia `Job` (los trabajos son efímeros y específicos del proyecto origen).

Coste: un proyecto con muchos assets puede tardar (copia de ficheros). Se expone en dos
modos para no bloquear la request:

- **Síncrono** para proyectos pequeños (respuesta directa con el `ProjectRead` nuevo).
- **Vía cola de jobs** (`type="duplicate_project"`, handler nuevo registrado en
  `app/jobs/handlers.py`) para proyectos grandes; devuelve un `Job` para polling, igual
  que render. La decisión (síncrono vs job) se toma por un umbral simple (nº de assets o
  tamaño total del workspace), documentado y configurable.

Lógica pura testeable: la función que construye el plan de copia (mapa de filas y de
rutas) se separa de la E/S de ficheros para poder testearla sin tocar disco.

### 1.2 Renombrar

Ya cubierto por `PATCH /projects/{id}` con `{ "name": "..." }`. No se añade endpoint
nuevo; se expone en la UI (acción "Renombrar" por proyecto). Se documenta como "ya
existe en el backend".

### 1.3 Carpetas / etiquetas (tags) + búsqueda/filtrado

Modelo: añadir a `Project` dos campos opcionales, portables en SQLite:

- `folder: str | None` (una "carpeta" lógica; agrupación simple de un nivel).
- `tags: list[str]` persistido como JSON (columna `JSON`, igual que `provider_overrides`).

Migración: el proyecto usa `init_db()` con `Base.metadata.create_all` (no hay Alembic).
Para BDs existentes sin estas columnas, se añade una migración ligera idempotente
(ALTER TABLE ADD COLUMN si falta) ejecutada en `init_db()`/arranque, o se documenta que
en local la BD se recrea. Se decide por ALTER idempotente para no perder datos del
usuario (se detalla en el feature).

Endpoints:

- `GET /projects?q=&tag=&folder=`: amplía `list_projects` con filtros opcionales:
  - `q`: substring case-insensitive sobre `name` y `prompt`.
  - `tag`: proyectos que contengan ese tag.
  - `folder`: proyectos de esa carpeta.
  - Mantiene compatibilidad: sin parámetros, devuelve todo como hoy (regresión).
- `PATCH /projects/{id}` admite `folder` y `tags` (ampliar `ProjectUpdate`).
- `GET /projects/tags` y `GET /projects/folders`: listas distintas existentes para poblar
  los filtros de la UI (opcionales; derivables en frontend, pero útiles para evitar
  cargar todo).

Esquemas: ampliar `ProjectRead`/`ProjectUpdate`/`ProjectCreate` con `folder` y `tags`
(defaults compatibles: `folder=None`, `tags=[]`).

### 1.4 Borrar proyecto (con limpieza del workspace + confirmación)

Ampliar `DELETE /projects/{id}` para que, además de borrar las filas (cascada ORM ya lo
hace), borre el workspace de media del proyecto de forma segura:

- Resolver `project_dir(id)`, verificar que `.resolve()` está DENTRO de
  `media_root().resolve()` (defensa contra rutas fuera de sitio) y solo entonces
  `shutil.rmtree(..., ignore_errors=False)` capturando errores para no dejar la BD
  inconsistente si el disco falla.
- La confirmación es responsabilidad de la UI (diálogo "¿Seguro?"); el backend borra sin
  más pasos (el DELETE ya es explícito).

### 1.5 UI de administración (Dashboard)

En `Dashboard.tsx`, por cada card de proyecto, añadir un menú de acciones real:

- **Renombrar** (`updateProject`), **Duplicar** (`duplicateProject` → si devuelve job,
  polling con el hook existente), **Borrar** (confirmación + `deleteProject`, refresco de
  la lista), **Editar tags/carpeta** (`updateProject`).
- Barra superior: buscador (`q`) + filtro por tag y por carpeta (llaman a `listProjects`
  con parámetros). Chips de tags en cada card.
- `lib/api.ts`: añadir `duplicateProject`, extender `ProjectUpdate`/`Project`/`listProjects`
  con `folder`/`tags`/filtros. Toda acción es una llamada real (sin estado simulado).

---

## 2. Control de calidad (QA report)

Objetivo: un reporte de calidad REAL por render que ejecute ffprobe/ffmpeg sobre el MP4 y
sobre el estado del proyecto, con checks `pass|warn|fail` y detalle, expuesto por API y
mostrado en el panel de render.

### 2.1 Checks

Cada check produce `{ id, label, status: "pass"|"warn"|"fail", detail, measured }`.

| id | Qué mide | Cómo (real) | Criterio |
|---|---|---|---|
| `loudness` | Loudness integrada del audio del MP4 | `VoiceMaster.measure_integrated(mp4)` (ebur128) | `pass` si |I − (−14)| ≤ 1 LU; `warn` si ≤ 2; `fail` si mayor o sin audio cuando debería haberlo |
| `black_frames` | Frames negros | ffmpeg `blackdetect=d=...:pic_th=...` sobre el MP4; parsea `black_start/black_end` del stderr | `pass` sin negros significativos; `warn` si hay negros cortos; `fail` si un tramo negro supera un umbral (p. ej. ≥ 1 s o al inicio) |
| `duration_match` | Coherencia de duración | Suma de `Scene.duration_s` (o duración del `EditProject`/timeline) vs duración real del MP4 vía ffprobe (`probe_media`) | `pass` si |Δ| ≤ tolerancia (p. ej. 0.5 s); `warn`/`fail` escalando |
| `resolution_aspect` | Resolución y aspect correctos | `probe_media` → `width`/`height`; comparar aspect con `Project.aspect_ratio` y, si procede, resolución con `Project.resolution` | `pass` si el aspect coincide (dentro de tolerancia por redondeo); `fail` si no |
| `subtitle_sync` | Subtítulos dentro de la duración | Cues del `EditProject` (pista `subtitle`) o del SRT: todos los `end_s` ≤ duración del MP4 (+ margen) y `start_s < end_s` | `pass` si todos dentro; `warn` si algún cue excede ligeramente; `fail` si excede de forma relevante |

Notas:

- Todos los límites/umbrales se definen como constantes documentadas (reutilizables en
  tests) y, donde tenga sentido, configurables por `AIV_QA_*` (p. ej.
  `AIV_QA_BLACK_MIN_S`), con defaults sensatos.
- El check de loudness reutiliza `VoiceMaster`/parsers de `master.py`; el de duración y
  resolución reutilizan `probe_media` de `storage/uploads.py`; los de subtítulos usan el
  `EditProject` ya persistido. No se introduce un nuevo patrón de invocación de ffmpeg.

### 2.2 Separación lógica pura / E/S (clave para tests)

- **Mediciones** (I/O): funciones que corren ffprobe/ffmpeg y devuelven números crudos
  (loudness, duración, lista de tramos negros, dimensiones). Testeables con un MP4 real
  generado con `lavfi` (como `test_render_engine.py`), o **saltadas con razón** si
  ffmpeg/ffprobe no están.
- **Evaluación** (pura): dado un conjunto de mediciones + metadatos del proyecto, produce
  la lista de checks con `status`/`detail`. 100% testeable sin ffmpeg (tablas de
  entrada → veredicto esperado). Esto permite cubrir TODA la lógica de clasificación
  `pass/warn/fail` en el sandbox aunque el render no corra.

Ubicación propuesta: `backend/app/engines/qa/` (nuevo paquete del motor de calidad), con
`checks.py` (evaluación pura) y `measure.py` (mediciones ffprobe/ffmpeg), reutilizando
`master.py`/`uploads.py`. Un `report.py` orquesta: mide sobre el MP4 del render y evalúa.

### 2.3 Shape del reporte

```json
{
  "project_id": 12,
  "asset_id": 345,
  "video_path": ".../project_12/video/final.mp4",
  "generated_at": "2025-...Z",
  "overall": "warn",
  "checks": [
    {
      "id": "loudness",
      "label": "Loudness integrada (-14 LUFS)",
      "status": "pass",
      "detail": "I medido = -13.8 LUFS (objetivo -14 ± 1)",
      "measured": { "integrated_lufs": -13.8, "target": -14.0 }
    },
    {
      "id": "black_frames",
      "label": "Frames negros",
      "status": "warn",
      "detail": "1 tramo negro de 0.4 s en t=0.0",
      "measured": { "segments": [[0.0, 0.4]] }
    }
  ]
}
```

`overall` = peor estado de los checks (`fail` > `warn` > `pass`).

### 2.4 Persistencia

Decisión: persistir el reporte en `Asset.meta` del `Asset(kind='video')` del render, bajo
la clave `qa_report` (no se crea tabla nueva). Justificación:

- El QA report está 1:1 con un render (un `Asset(kind='video')`); guardarlo en su `meta`
  lo ata al artefacto exacto y queda disponible al listar assets del proyecto.
- Evita una migración de esquema nueva y encaja con el patrón existente (el render ya
  escribe `meta` rico).
- Se conserva el historial: cada render produce su propio `Asset` con su `qa_report`, así
  que los reportes de renders anteriores no se pisan.

(Alternativa considerada y descartada por ahora: tabla `QAReport` dedicada. Aporta
consultas por check pero exige migración y no hay requisito de consulta cruzada; se deja
anotada como vía futura si se necesitara análisis agregado.)

### 2.5 Ejecución y exposición

- **Cuándo se genera:** al terminar el render, dentro del flujo del job (en
  `render_handler`/`edit_export_handler` o en un paso posterior encadenado), se corre el
  QA report sobre el MP4 recién producido y se escribe en el `meta` del `Asset`. Si
  ffprobe/ffmpeg fallan o el render no está disponible, el QA report NO se inventa: se
  omite o se marca el motivo (coherente con la política de "nunca simular").
- **Endpoint de consulta:** `GET /projects/{id}/render/qa` devuelve el `qa_report` del
  último `Asset(kind='video')` (404 si no hay render; 409/mensaje claro si el render
  existe pero no se pudo medir). Opcional: `POST /projects/{id}/render/qa` para
  recomputar bajo demanda sobre el MP4 existente (útil si se añaden checks después).
- **UI:** en `RenderPanel.tsx`, bajo el vídeo final, un bloque "Control de calidad" con la
  lista de checks (icono por `status`, `label`, `detail`) y un `StatusBadge` global
  (`overall`). Reutiliza `Card`/`StatusBadge`/`ErrorBanner`. `lib/api.ts` añade
  `fetchRenderQA(projectId)`.

---

## 3. Herramientas de edición inteligente (honesto, determinista)

Sin "IA mágica". Todo determinista y verificable.

### 3.1 Auto-corte por silencios como acción del editor

`engines/voice/trim.py` ya implementa el recorte con re-mapeo de timings. Se expone como
operación puntual:

- Nueva operación del editor que recorta silencios de un clip de audio (voz) seleccionado
  y ajusta su `source_in/out`/duración (y, si hay subtítulos asociados, re-mapea sus
  cues con la lógica pura ya existente). Se integra en el dispatch de operaciones
  (`engines/editor/operations.py` `OPERATIONS`), creando una `Version` para undo/redo como
  el resto.
- Alternativa/duplicado de exposición: un endpoint de análisis que devuelve los tramos de
  silencio detectados (`parse_silencedetect`) para que la UI muestre "quitar silencios"
  con preview. La detección real usa ffmpeg; la construcción de tramos y el re-mapeo son
  lógica pura ya testeada en `test_silence_trim.py` (ampliar si hace falta).

### 3.2 Sugerencias deterministas

Un verificador del estado del proyecto/edición que produce avisos accionables (mismo
shape de check que el QA, `status`/`detail`), 100% lógica pura:

- Escenas sin imagen (no hay `Asset(kind='image')` para una escena).
- Subtítulos cuyos cues exceden la duración de su escena/clip.
- Clips solapados en una pista que no deberían solaparse.
- (Opcional) Escenas con `duration_s` sospechosa (0 o negativa), proyecto sin voz, etc.

Se expone como `GET /projects/{id}/suggestions` (o se integra en el mismo panel de QA
como sección "Sugerencias"). Al ser puro sobre el estado en BD/`EditProject`, es
completamente testeable sin ffmpeg.

---

## 4. Producción cinematográfica (nota honesta)

- **2D:** lo cinematográfico real disponible es limitado y YA existe en el editor: Ken
  Burns (zoom/paneo lento) y `transform` por clip (escala/posición/encuadre), compilados
  a Remotion. Para el Bloque 4, a lo sumo se **expone/pule** Ken Burns por escena si
  encaja barato (presets de Ken Burns aplicables desde la UI del editor/storyboard,
  reutilizando el `VisualEffect` ya modelado). NO se prometen cámaras cinematográficas 2D
  (travelling, aérea, contrapicado) que no existen como tales en el pipeline 2D.
- **3D (Blender):** es el terreno natural de las cámaras cinematográficas; el Motor 3D ya
  tiene su arquitectura (`docs/ENGINE-3D-ARCHITECTURE.md`). Las cámaras cinematográficas
  3D completas quedan como iteración del Motor 3D, NO como entregable de este bloque.
- Se documenta explícitamente qué entra (exponer Ken Burns 2D, opcional) y qué se difiere
  (cámaras 3D), para no vender funcionalidad inexistente.

---

## 5. Protección / regresión y por qué login/multiusuario queda fuera

- **No romper la vía de pago:** no se toca la capa de proveedores ni su conmutabilidad
  (`providers/`, `provider_overrides`, backends de pago). El QA mide el MP4 producido,
  sea cual sea el proveedor usado.
- **No romper el pipeline de generación/render:** `compose`/`compose_edit` y el
  `TimelineBuilder` no se modifican de forma que alteren su salida. El QA se ejecuta
  DESPUÉS del render, sobre su salida; las sugerencias son de solo lectura sobre el estado.
- **Compatibilidad de endpoints existentes:** `GET /projects` sin parámetros sigue
  devolviendo todo; `DELETE`/`PATCH` mantienen su contrato (se amplían, no se rompen);
  los nuevos campos `folder`/`tags` tienen defaults compatibles.
- **Baseline verde:** el primer feature fija el baseline (527 passed / 3 skipped con
  ffmpeg) y toda feature posterior debe mantenerlo. El render Remotion headless es
  intermitente en el sandbox: los tests que lo requieran se saltan con razón, nunca
  simulan.
- **Login/multiusuario/suscripción fuera:** por el propio roadmap
  (`docs/EDITOR-ROADMAP.md`), esas funciones pertenecen a la **Fase 4 comercial**. La app
  es monousuario local (corre en el PC del usuario); añadir auth ahora sería alcance no
  pedido y contrario al roadmap.

---

## 6. Desglose de features (resumen)

El desglose verificable vive en `.agents/tasks/task-admin-calidad/`. Resumen:

- **FEAT-001 — Baseline verde.** Instalar/activar entorno backend y frontend, correr la
  suite existente con `AIV_FFMPEG_BIN`/`AIV_FFPROBE_BIN`, confirmar baseline (527/3).
- **FEAT-002 — Admin backend.** Duplicar (copia profunda + media, síncrono + job),
  campos `folder`/`tags` (+ migración idempotente), búsqueda/filtrado en `GET /projects`,
  borrado con limpieza segura del workspace. Esquemas y tests (lógica pura + API).
- **FEAT-003 — Admin UI (Dashboard).** Acciones por proyecto (renombrar/duplicar/borrar/
  tags-carpeta), buscador y filtros, helpers de `lib/api.ts`. build/lint/typecheck.
- **FEAT-004 — QA report backend.** Paquete `engines/qa/` (medición ffprobe/ffmpeg +
  evaluación pura), checks (loudness, black_frames, duration_match, resolution_aspect,
  subtitle_sync), shape del reporte, persistencia en `Asset.meta.qa_report`, ejecución
  tras el render y endpoint `GET /projects/{id}/render/qa`. Tests puros + tests ffmpeg
  (lavfi) con skip razonado.
- **FEAT-005 — QA UI (RenderPanel).** Bloque "Control de calidad" con lista de checks y
  badge global; `fetchRenderQA` en `lib/api.ts`. build/lint/typecheck.
- **FEAT-006 — Edición inteligente.** Operación/endpoint de auto-corte por silencios
  (reutiliza `trim.py`, crea `Version`) + sugerencias deterministas
  (`GET /projects/{id}/suggestions`). Tests puros + ffmpeg con skip razonado.

Cada feature define `acceptance_criteria` y `verification` con comandos reales:
`pytest` del backend con `AIV_FFMPEG_BIN`/`AIV_FFPROBE_BIN` apuntando a
`/projects/sandbox/.localbin`, y `npm run build`/`lint`/`typecheck` en el frontend.

---

## 7. Riesgos

- **Render intermitente en sandbox:** el QA report completo depende de un MP4; cuando el
  render de Remotion no corre (sin Chromium), los tests de medición se saltan con razón y
  se valida la evaluación pura. En el PC del usuario se ejercita de punta a punta.
- **Migración de columnas nuevas (`folder`/`tags`):** sin Alembic, el ALTER idempotente
  en arranque debe ser cuidadoso en SQLite (comprobar columnas con `PRAGMA table_info`).
  Riesgo bajo, mitigado con tests sobre BD recién creada y sobre BD "vieja" simulada.
- **Duplicado de proyectos grandes:** copia de muchos ficheros puede tardar; el modo job
  evita bloquear la API. Reescritura de rutas en timeline/EditProject debe cubrir todos
  los lugares donde aparecen rutas de recurso (riesgo de rutas rotas en la copia).
- **Borrado destructivo:** `rmtree` del workspace debe verificar el contenedor
  (`media_root`) antes de borrar para evitar borrar fuera de sitio. Mitigado con el chequeo
  de contención y tests sobre rutas controladas.
