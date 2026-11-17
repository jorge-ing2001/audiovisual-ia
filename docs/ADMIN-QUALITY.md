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

#### 1.1.1 Identificadores nuevos y preservación de relaciones

La duplicación es una **copia profunda con identidades nuevas**: `Project`, `Scene`,
`Asset`, `Timeline`/`EditProject` y `Version` del origen generan filas NUEVAS con `id`
nuevos (autoincrement de SQLite al hacer `flush`), nunca se reutilizan los del origen. Lo
que se PRESERVA son las relaciones internas, re-mapeando las FKs del origen a los ids
nuevos:

- `project_id` de cada `Scene`/`Asset`/`Timeline`/`Version` → `new_project.id`.
- `scene_id` de cada `Asset` → el `id` de la escena duplicada (diccionario
  `scene_map: old_scene_id → new_scene_id`). Un `Asset` con `scene_id=None` se mantiene
  `None`.
- Referencias embebidas en JSON: dentro de `Timeline.json` y del `EditProject`
  persistido (`store.py`) pueden aparecer rutas de recurso que incrustan
  `project_{old}`; se reescriben a `project_{new}` con el mapa de rutas (ver 1.1.2). Si el
  JSON incrustara ids de escena/asset del origen, se re-mapean con `scene_map`/`asset_map`.

#### 1.1.2 Algoritmo determinista (mapas, orden de copia, punto de commit)

Función PURA `build_duplication_plan(project, scenes, assets, timelines, scripts, new_id)`
que NO toca disco ni BD; devuelve:

- `row_plan`: las filas a crear por tabla, ya con FKs re-mapeadas (usando `scene_map`,
  que se puede resolver en dos pasadas: primero se reservan placeholders de `old→new`
  para escenas, luego se aplican a los assets).
- `path_map`: lista de pares `(src_abs, dst_abs)` de ficheros a copiar, derivada de
  reemplazar el prefijo `project_dir(old_id)` → `project_dir(new_id)` en cada `Asset.path`
  y en cada ruta de recurso detectada en los JSON de timeline/EditProject.
- `json_rewrites`: para cada `Timeline`/`EditProject`, el JSON con las rutas y los ids ya
  reescritos.

Orden de ejecución de la E/S (`run_duplication_plan`), pensado para que un fallo a mitad
sea recuperable:

1. `INSERT` del `Project` nuevo (status `draft`, `created_at` nuevo, `name` = el pasado o
   `"<name> (copia)"`) y `flush` para fijar `new_id`.
2. `ensure_project_workspace(new_id)` (crea el árbol de subcarpetas destino).
3. `INSERT` de `Scene` (conservando `order`) y `flush` para poblar `scene_map`.
4. `INSERT` de `Asset` (con `scene_id` re-mapeado y `path` reescrito), `Timeline`/
   `EditProject` (con `json` reescrito) y `Script`. NO se copia ningún `Job`.
5. Copia de ficheros de `path_map` con `shutil.copy2` (crea subdirectorios destino si
   faltan). Se copia DESPUÉS de tener las filas para que el `path_map` final use las rutas
   ya reescritas.
6. **Punto de commit único:** `session.commit()` al final, cuando filas y ficheros están
   listos. Hasta ese commit, la transacción de BD es revertible.

#### 1.1.3 Rollback y limpieza de ficheros parciales

Si cualquier paso falla a mitad, la operación debe dejar el sistema como estaba (sin
proyecto nuevo "a medias"):

- **BD:** todo el trabajo ocurre dentro de una única transacción; ante excepción se hace
  `session.rollback()` (o se deja que `session_scope` lo haga), de modo que ninguna fila
  nueva queda persistida.
- **Disco:** la copia de ficheros no es transaccional, así que el handler registra el
  workspace destino recién creado y, ante fallo, borra `project_dir(new_id)` con la MISMA
  rutina de borrado seguro de 1.4 (resolver + contención en `media_root`). La limpieza
  solo borra el directorio del proyecto NUEVO, nunca el origen.
- **Orden de deshacer:** primero `rollback` de BD, luego `rmtree` seguro del workspace
  parcial. El resultado es atómico de cara al usuario: o existe un proyecto duplicado
  completo, o no existe nada nuevo.

#### 1.1.4 Modo job con seguimiento de estado (proyectos grandes)

Para proyectos por encima del umbral, el endpoint encola un `Job`
`type="duplicate_project"` con `project_id` del origen y `payload={new_name}`, y devuelve
el `Job` (polling igual que render). El handler nuevo en `app/jobs/handlers.py`:

- Actualiza `Job.progress` conforme avanza la copia de ficheros (p. ej. fracción de
  ficheros copiados sobre el total de `path_map`), usando el patrón de progreso ya
  presente en los handlers existentes.
- Al terminar, `JobQueue.complete(job_id, result={"project_id": new_id})`.
- Ante fallo, ejecuta el rollback+limpieza de 1.1.3 y llama a `JobQueue.fail(...)` con un
  mensaje claro; el proyecto nuevo NO queda a medias. El seguimiento de estado del `Job`
  (`pending`/`running`/`succeeded`/`failed` + `progress`) es el canal de observabilidad.

### 1.2 Renombrar

Ya cubierto por `PATCH /projects/{id}` con `{ "name": "..." }`. No se añade endpoint
nuevo; se expone en la UI (acción "Renombrar" por proyecto). Se documenta como "ya
existe en el backend".

### 1.3 Carpetas / etiquetas (tags) + búsqueda/filtrado

Modelo: añadir a `Project` dos campos opcionales, portables en SQLite:

- `folder: str | None` (una "carpeta" lógica; agrupación simple de un nivel).
- `tags: list[str]` persistido como JSON (columna `JSON`, igual que `provider_overrides`).

Migración idempotente (SQLite, sin Alembic): ver §1.6. Se decide por `ALTER TABLE ADD
COLUMN` idempotente para no perder datos del usuario; los defaults de las columnas nuevas
son compatibles con proyectos existentes (`folder=NULL`, `tags=NULL`→`[]` en lectura).

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
hace), borre el workspace de media del proyecto de forma segura. La rutina de borrado
seguro (`safe_rmtree(target)`, reutilizada también por la limpieza de duplicación 1.1.3)
aplica estas comprobaciones **en este orden**, y solo si TODAS pasan ejecuta el borrado:

1. **Resolución de rutas absolutas:** `root = media_root().resolve()` y
   `target = project_dir(id).resolve()`. `Path.resolve()` normaliza `..`, rutas relativas
   y resuelve symlinks del propio `target`, de modo que se compara la ruta física real.
2. **Contención real dentro de `media_root`:** exigir
   `target.is_relative_to(root)` (Python 3.11). Si `target` cae fuera de `media_root`, se
   rechaza (nunca se borra fuera del directorio autorizado).
3. **Impedir borrar la raíz:** rechazar si `target == root` (no se permite borrar el
   propio `media_root`); el `target` debe ser un subdirectorio estricto (`project_{id}`).
4. **Defensa frente a symlinks:** si `project_dir(id)` (sin resolver) es un symlink, o si
   tras resolver symlinks el destino sale de `media_root` (un enlace que apunta fuera), se
   rechaza; no se sigue el enlace para borrar su objetivo. En la práctica, el paso 1
   resuelve el symlink y el paso 2 vuelve a verificar la contención del destino REAL, así
   que un enlace que apunta fuera de `media_root` queda rechazado por contención.
5. **No borrar con operaciones activas:** ANTES de tocar BD o disco, comprobar que el
   proyecto no tenga `Job` en estado `pending` o `running` asociado
   (`Job.project_id == id`). Si los hay, se rechaza con **409 Conflict** y un mensaje
   claro ("el proyecto tiene operaciones en curso"); no se borra nada.
6. Solo entonces: borrar filas (cascada ORM) y `shutil.rmtree(target,
   ignore_errors=False)` capturando errores de disco para no dejar la BD inconsistente.

La confirmación de usuario es responsabilidad de la UI (diálogo "¿Seguro?"); el backend
aplica las comprobaciones anteriores de forma incondicional.

### 1.5 UI de administración (Dashboard)

En `Dashboard.tsx`, por cada card de proyecto, añadir un menú de acciones real:

- **Renombrar** (`updateProject`), **Duplicar** (`duplicateProject` → si devuelve job,
  polling con el hook existente), **Borrar** (confirmación + `deleteProject`, refresco de
  la lista), **Editar tags/carpeta** (`updateProject`).
- Barra superior: buscador (`q`) + filtro por tag y por carpeta (llaman a `listProjects`
  con parámetros). Chips de tags en cada card.
- `lib/api.ts`: añadir `duplicateProject`, extender `ProjectUpdate`/`Project`/`listProjects`
  con `folder`/`tags`/filtros. Toda acción es una llamada real (sin estado simulado).

### 1.6 Migración idempotente de SQLite (columnas `folder`/`tags`)

El proyecto arranca la BD con `init_db()` en `backend/app/storage/db.py`, que llama a
`Base.metadata.create_all` (crea tablas que faltan, pero NO añade columnas a tablas ya
existentes). No hay Alembic. Para que una BD de un proyecto ya en uso incorpore
`folder`/`tags` sin recrearse ni perder datos, se añade una migración ligera e
**idempotente** que se ejecuta al final de `init_db()` (justo después de `create_all`),
de modo que corre en cada arranque de forma segura y repetible.

Mecanismo (`_ensure_project_columns(engine)`):

1. Solo para el dialecto SQLite (`engine.dialect.name == "sqlite"`); en otros dialectos
   no hace nada (Postgres se gestionaría con migraciones propias en Fase 4).
2. Lee las columnas existentes de la tabla con `PRAGMA table_info('projects')` y construye
   el conjunto de nombres de columna presentes.
3. Para cada columna objetivo (`folder`, `tags`) que NO esté en ese conjunto, ejecuta un
   `ALTER TABLE projects ADD COLUMN ...` con un default compatible
   (`folder TEXT`, `tags JSON`/`TEXT`, ambas NULL por defecto). Si la columna ya existe,
   NO ejecuta el `ALTER` (evita el error "duplicate column name" de SQLite).

Por qué es seguro repetirlo: la decisión de alterar se toma SIEMPRE a partir del estado
real del esquema (`PRAGMA table_info`) justo antes de cada `ALTER`, nunca a ciegas.
Ejecutarlo dos veces seguidas es un no-op en la segunda pasada (las columnas ya existen).
Compatibilidad: los proyectos existentes leen `folder=NULL` y `tags=NULL`, que las capas
de esquema normalizan a `folder=None` y `tags=[]`, por lo que su comportamiento no cambia.

---

## 2. Control de calidad (QA report)

Objetivo: un reporte de calidad REAL por render que ejecute ffprobe/ffmpeg sobre el MP4 y
sobre el estado del proyecto, con checks `pass|warn|fail` y detalle, expuesto por API y
mostrado en el panel de render.

### 2.1 Estados de un check: `pass` / `warn` / `fail` / `skipped`

Cada check produce `{ id, label, status, detail, measured, threshold }` donde `status` es
uno de CUATRO valores (no tres):

- **`pass`**: la medición cumple el criterio dentro de tolerancia.
- **`warn`**: desviación menor, aceptable pero digna de aviso.
- **`fail`**: desviación relevante; el render no cumple el criterio.
- **`skipped`**: el check NO se pudo evaluar (falta ffmpeg/ffprobe, no hay MP4, el MP4
  no tiene audio para medir loudness, el proyecto no tiene subtítulos que validar, etc.).
  Un `skipped` lleva SIEMPRE su razón en `detail` (ver §2.3 `skip_reason`).

Regla fundamental: **un control omitido (`skipped`) NO cuenta como aprobado.** No se
promueve a `pass` por ausencia de datos; se refleja explícitamente como `skipped` y
pesa en el `overall` según §2.2.

### 2.2 Umbrales y tolerancias de los 5 controles

Todos los umbrales son constantes documentadas en `engines/qa/checks.py`, reutilizables
en tests, y configurables por entorno `AIV_QA_*` donde tenga sentido (defaults abajo).

| id | Qué mide y cómo (real) | `pass` | `warn` | `fail` | `skipped` |
|---|---|---|---|---|---|
| `loudness` | Loudness integrada (LUFS) del audio del MP4 vía `VoiceMaster.measure_integrated` (ebur128). Objetivo `−14 LUFS`. | `|I − (−14)| ≤ 1.0 LU` | `1.0 < |Δ| ≤ 2.0 LU` | `|Δ| > 2.0 LU` | sin ffmpeg, o el MP4 no tiene pista de audio (`skip_reason`) |
| `black_frames` | Frames negros vía ffmpeg `blackdetect=d=AIV_QA_BLACK_MIN_S:pic_th=...`; parsea `black_start/black_end`. | 0 tramos negros (más allá del umbral de detección) | algún tramo negro con duración `< 1.0 s` (`AIV_QA_BLACK_FAIL_S`) | algún tramo `≥ 1.0 s` (se reporta la duración y el `t` de inicio en segundos) | sin ffmpeg, o no hay MP4 |
| `duration_match` | Duración esperada (suma de `Scene.duration_s` o `EditProject.duration_s`) vs duración real del MP4 (`probe_media`). | `|Δ| ≤ 0.5 s` **y** `|Δ| ≤ 2%` de la esperada | `0.5 s < |Δ| ≤ 1.0 s` o `2% < |Δ| ≤ 5%` | `|Δ| > 1.0 s` **y** `> 5%` | sin ffprobe/MP4, o sin duración esperada conocida |
| `resolution_aspect` | `probe_media` → `width`/`height`; aspect comparado con `Project.aspect_ratio` (y resolución con `Project.resolution` si está). | aspect coincide dentro de `±0.01` por redondeo (y resolución exacta si se conoce) | n/a (este control no usa `warn`) | aspect no coincide, o resolución distinta de la declarada | sin ffprobe/MP4 |
| `subtitle_sync` | Cues del `EditProject` (pista `subtitle`) o del SRT: cada cue dentro de la duración del MP4 y `start_s < end_s`. | todos los cues con `0 ≤ start_s < end_s ≤ dur + 0.25 s` (margen `AIV_QA_SUB_MARGIN_S`) | algún cue excede el margen en `≤ 0.5 s`, o micro-solape `≤ 0.1 s` | algún cue excede en `> 0.5 s`, `end_s ≤ start_s`, o solapes relevantes | el proyecto no tiene subtítulos (nada que validar), o no hay MP4 para conocer la duración |

Notas de implementación:

- El check de loudness reutiliza `VoiceMaster`/parsers de `master.py`; duración y
  resolución reutilizan `probe_media` de `storage/uploads.py`; subtítulos usan el
  `EditProject` ya persistido. No se introduce un nuevo patrón de invocación de ffmpeg.
- Las constantes de umbral viven en `checks.py` (p. ej. `LOUDNESS_WARN_LU=1.0`,
  `LOUDNESS_FAIL_LU=2.0`, `DURATION_WARN_S=0.5`, `DURATION_FAIL_S=1.0`,
  `DURATION_WARN_PCT=0.02`, `DURATION_FAIL_PCT=0.05`, `BLACK_MIN_S`, `BLACK_FAIL_S=1.0`,
  `SUB_MARGIN_S=0.25`) y se exponen en `thresholds` del reporte (§2.3).

### 2.3 Regla de `overall` (skipped nunca es pass)

`overall` se calcula de forma explícita y conservadora a partir de los estados de los
checks, con esta precedencia:

1. Si **algún** check es `fail` → `overall = "fail"`.
2. Si no hay `fail` pero **algún** check es `warn` → `overall = "warn"`.
3. Si no hay `fail` ni `warn` pero **algún** check es `skipped` → `overall =
   "pass_with_skips"` (nunca `pass` a secas: significa "lo evaluado pasó, pero el reporte
   está incompleto"). El reporte lista en `skipped_checks` cuáles se omitieron y por qué.
4. Si TODOS los checks son `pass` (ninguno `skipped`) → `overall = "pass"`.

Es decir, `fail > warn > skipped > pass`, y un `skipped` degrada un `pass` global a
`pass_with_skips`. Un reporte donde todo falla por ausencia de ffmpeg será
`pass_with_skips`/`fail` según corresponda, nunca `pass`.

### 2.4 Separación lógica pura / E/S (clave para tests)

- **Mediciones** (I/O): funciones que corren ffprobe/ffmpeg y devuelven números crudos
  (loudness, duración, lista de tramos negros, dimensiones). Testeables con un MP4 real
  generado con `lavfi` (como `test_render_engine.py`), o **saltadas con razón** si
  ffmpeg/ffprobe no están (devuelven el `skip_reason` que el check convierte en
  `status="skipped"`).
- **Evaluación** (pura): dado un conjunto de mediciones + metadatos del proyecto, produce
  la lista de checks con `status`/`detail` y el `overall`. 100% testeable sin ffmpeg
  (tablas de entrada → veredicto esperado). Esto permite cubrir TODA la lógica de
  clasificación `pass/warn/fail/skipped` y la regla de `overall` en el sandbox aunque el
  render no corra. La evaluación pura clasifica a partir de mediciones DADAS, sin invocar
  ffmpeg en ningún caso.

Ubicación propuesta: `backend/app/engines/qa/` (nuevo paquete del motor de calidad), con
`checks.py` (evaluación pura) y `measure.py` (mediciones ffprobe/ffmpeg), reutilizando
`master.py`/`uploads.py`. Un `report.py` orquesta: mide sobre el MP4 del render y evalúa.

### 2.5 Shape del reporte (trazabilidad completa)

El `qa_report` incluye los datos necesarios para auditar QUÉ render se midió, CUÁNDO, con
QUÉ motor y QUÉ umbrales, y el resultado de cada check (incluidas las razones de omisión):

```json
{
  "schema": "qa_report/v1",
  "qa_engine_version": "1.0.0",
  "project_id": 12,
  "asset_id": 345,
  "video_path": ".../project_12/video/final.mp4",
  "render_hash": "sha256:ab12…",
  "generated_at": "2025-01-30T12:34:56Z",
  "overall": "pass_with_skips",
  "thresholds": {
    "loudness": { "target_lufs": -14.0, "warn_lu": 1.0, "fail_lu": 2.0 },
    "black_frames": { "black_min_s": 0.1, "black_fail_s": 1.0 },
    "duration_match": { "warn_s": 0.5, "fail_s": 1.0, "warn_pct": 0.02, "fail_pct": 0.05 },
    "resolution_aspect": { "aspect_tol": 0.01 },
    "subtitle_sync": { "margin_s": 0.25 }
  },
  "checks": [
    {
      "id": "loudness",
      "label": "Loudness integrada (-14 LUFS)",
      "status": "pass",
      "detail": "I medido = -13.8 LUFS (objetivo -14 ± 1)",
      "measured": { "integrated_lufs": -13.8 },
      "threshold": { "target_lufs": -14.0, "warn_lu": 1.0, "fail_lu": 2.0 }
    },
    {
      "id": "black_frames",
      "label": "Frames negros",
      "status": "skipped",
      "detail": "ffmpeg no disponible: no se pudo ejecutar blackdetect",
      "skip_reason": "ffmpeg_unavailable",
      "measured": null,
      "threshold": { "black_min_s": 0.1, "black_fail_s": 1.0 }
    }
  ],
  "skipped_checks": [
    { "id": "black_frames", "skip_reason": "ffmpeg_unavailable" }
  ]
}
```

Campos de trazabilidad (requeridos):

- `generated_at`: timestamp ISO 8601 UTC de la ejecución del QA.
- `asset_id` + `render_hash`: identifican el render concreto medido. `render_hash` es un
  hash del fichero MP4 (p. ej. `sha256`); junto con `asset_id` permite detectar si el
  reporte corresponde a ESE fichero y no a otro.
- `qa_engine_version`: versión del motor de QA (constante en `engines/qa/`); permite saber
  con qué lógica/umbrales se evaluó un reporte antiguo.
- `thresholds`: la configuración de umbrales EFECTIVA usada en esa ejecución (resuelta de
  los defaults + overrides `AIV_QA_*`).
- `checks[]`: por cada check, `status` (pass/warn/fail/skipped), `detail`, `measured`
  (medición cruda, `null` si skipped), `threshold` aplicado y, si procede, `skip_reason`.
- `skipped_checks[]`: resumen de los controles omitidos con su razón (para la regla de
  frescura y el `overall=pass_with_skips`).

### 2.6 Persistencia

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

### 2.7 Frescura del reporte (no mostrar reportes obsoletos)

El reporte se asocia SIEMPRE a un render concreto por `asset_id` (y `render_hash`). El
endpoint y la UI aplican esta regla para no presentar un reporte viejo como si fuera el
del último render:

1. Se localiza el `Asset(kind='video')` MÁS RECIENTE del proyecto (el "último render").
2. Si ese asset tiene `meta.qa_report` con `asset_id` == su propio id, el reporte es
   fresco y se devuelve.
3. Si el último render NO tiene `qa_report` (p. ej. el QA se omitió por falta de ffmpeg),
   el endpoint responde indicando "sin reporte para el último render" (no busca un reporte
   de un render anterior para rellenar el hueco). La UI muestra ese estado explícito.
4. Opcionalmente, si se dispone del fichero, se compara `render_hash` del reporte con el
   hash actual del MP4; si no coinciden, el reporte se considera obsoleto (el fichero
   cambió) y se marca como tal en vez de presentarse como actual.

Nunca se muestra el `qa_report` de un render antiguo etiquetado como el del render actual.

### 2.8 Ejecución y exposición

- **Cuándo se genera:** al terminar el render, dentro del flujo del job (en
  `render_handler`/`edit_export_handler` o en un paso posterior encadenado), se corre el
  QA report sobre el MP4 recién producido y se escribe en el `meta` del `Asset`. Si
  ffprobe/ffmpeg fallan o el render no está disponible, el QA report NO se inventa: los
  checks afectados quedan `skipped` con su `skip_reason` (coherente con la política de
  "nunca simular").
- **Endpoint de consulta:** `GET /projects/{id}/render/qa` devuelve el `qa_report` del
  ÚLTIMO `Asset(kind='video')` aplicando la regla de frescura §2.7 (404 si no hay render;
  respuesta clara "sin reporte para el último render" si el último render no tiene
  `qa_report`). Opcional: `POST /projects/{id}/render/qa` para recomputar bajo demanda
  sobre el MP4 existente (útil si se añaden checks después).
- **UI:** en `RenderPanel.tsx`, bajo el vídeo final, un bloque "Control de calidad" con la
  lista de checks (icono por `status` incluido `skipped`, `label`, `detail`) y un
  `StatusBadge` global (`overall`, incluido `pass_with_skips`). Si el último render no
  tiene reporte, se indica explícitamente en vez de mostrar uno viejo. Reutiliza
  `Card`/`StatusBadge`/`ErrorBanner`. `lib/api.ts` añade `fetchRenderQA(projectId)`.

---

## 3. Herramientas de edición inteligente (honesto, determinista)

Sin "IA mágica". Todo determinista y verificable.

### 3.1 Auto-corte por silencios como acción del editor

`engines/voice/trim.py` ya implementa el recorte con re-mapeo de timings. Se expone como
operación puntual, **reversible y no destructiva**:

- Nueva operación del editor que recorta silencios de un clip de audio (voz) seleccionado
  y ajusta su `source_in/out`/duración. Se integra en el dispatch de operaciones
  (`engines/editor/operations.py` `OPERATIONS`).
- **Crea una nueva `Version`** del `EditProject` a través de `save_edit_project`
  (`engines/editor/store.py`), igual que el resto de operaciones del editor. El estado
  anterior queda en el historial.
- **Preserva tiempos y referencias de subtítulos:** si el clip tiene subtítulos
  asociados, sus cues se re-mapean al nuevo eje temporal con la lógica pura ya existente
  (`SilenceTrimmer.remap_timings`/`speech_segments`), de modo que voz y subtítulos no se
  desalinean tras el corte.
- **Es reversible:** como la operación produce una `Version` nueva, el usuario puede
  deshacerla con el `undo` del editor (`store.undo`), que restaura el `EditProject`
  anterior (clip y cues originales). No se sobrescribe destructivamente el estado previo.
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
- **FEAT-002 — Admin backend.** Duplicar (ids nuevos + relaciones preservadas, copia de
  media y reescritura de rutas, con rollback+limpieza ante fallo y modo job con
  seguimiento de estado para proyectos grandes), campos `folder`/`tags` (+ migración
  idempotente con `PRAGMA table_info`), búsqueda/filtrado en `GET /projects`, borrado
  seguro (resolve → contención → no-raíz → symlinks → no-si-operaciones-activas).
  Esquemas y tests (lógica pura + API + regresión).
- **FEAT-003 — Admin UI (Dashboard).** Acciones por proyecto (renombrar/duplicar/borrar/
  tags-carpeta), buscador y filtros, helpers de `lib/api.ts`. build/lint/typecheck.
- **FEAT-004 — QA report backend.** Paquete `engines/qa/` (medición ffprobe/ffmpeg +
  evaluación pura), 5 checks con umbrales explícitos y 4 estados (pass/warn/fail/skipped),
  regla de `overall` conservadora (skipped degrada a `pass_with_skips`, nunca `pass`),
  shape completo del reporte con trazabilidad (`generated_at`, `asset_id`/`render_hash`,
  `qa_engine_version`, `thresholds`, resultados y `skip_reason`), persistencia en
  `Asset.meta.qa_report`, regla de frescura, ejecución tras el render y endpoint
  `GET /projects/{id}/render/qa`. Tests de evaluación pura (sin ffmpeg) + tests ffmpeg
  (lavfi) con skip razonado.
- **FEAT-005 — QA UI (RenderPanel).** Bloque "Control de calidad" con lista de checks y
  badge global; `fetchRenderQA` en `lib/api.ts`. build/lint/typecheck.
- **FEAT-006 — Edición inteligente.** Operación/endpoint de auto-corte por silencios
  (reutiliza `trim.py`, crea `Version`, re-mapea cues de subtítulos y es reversible vía
  undo del editor) + sugerencias deterministas (`GET /projects/{id}/suggestions`). Tests
  puros + ffmpeg con skip razonado.

Cada feature define `acceptance_criteria` y `verification` con comandos reales:
`pytest` del backend con `AIV_FFMPEG_BIN`/`AIV_FFPROBE_BIN` apuntando a
`/projects/sandbox/.localbin`, y `npm run build`/`lint`/`typecheck` en el frontend.

---

## 7. Riesgos

- **Render intermitente en sandbox:** el QA report completo depende de un MP4; cuando el
  render de Remotion no corre (sin Chromium), los tests de medición se saltan con razón y
  se valida la evaluación pura. En el PC del usuario se ejercita de punta a punta.
- **Migración de columnas nuevas (`folder`/`tags`):** sin Alembic, el ALTER idempotente
  en arranque comprueba columnas con `PRAGMA table_info` antes de cada `ADD COLUMN`
  (§1.6). Riesgo bajo, mitigado con tests que ejecutan la migración DOS veces sobre una BD
  "vieja" (sin las columnas) verificando que no falla ni duplica columnas.
- **Duplicado de proyectos grandes:** copia de muchos ficheros puede tardar; el modo job
  con seguimiento de estado evita bloquear la API. Reescritura de rutas en
  timeline/EditProject debe cubrir todos los lugares donde aparecen rutas de recurso
  (riesgo de rutas rotas en la copia). Un fallo a mitad dispara rollback de BD + limpieza
  del workspace parcial (§1.1.3), de modo que no queda un proyecto nuevo "a medias";
  cubierto por un test de fallo inyectado durante la copia.
- **Borrado destructivo:** `safe_rmtree` aplica resolve → contención en `media_root` →
  no-raíz → defensa de symlinks → no-si-operaciones-activas antes de borrar (§1.4).
  Mitigado con tests de rutas controladas, incluido un symlink que apunta fuera de
  `media_root` (debe rechazarse) y un proyecto con un `Job` activo (409).
- **Reportes QA obsoletos:** el `qa_report` se ata a `asset_id`/`render_hash` del render
  concreto y la regla de frescura (§2.7) evita presentar un reporte viejo como el actual;
  cubierto por un test de persistencia/recuperación asociada al render correcto.
