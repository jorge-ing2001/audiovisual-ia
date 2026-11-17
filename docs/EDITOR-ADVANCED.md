# Editor Avanzado (Bloque 1) — Diseño

> Estado: **PLAN / diseño**. Este documento describe cómo se implementa el **Bloque 1 —
> Editor Avanzado** del roadmap (`docs/EDITOR-ROADMAP.md`) SOBRE el modelo multipista
> `EditProject` ya consolidado en `main` (ver `docs/EDITOR-MULTITRACK-ARCHITECTURE.md`).
> La implementación se hace tarea por tarea desde `.agents/tasks/task-editor-avanzado/`.

Documentos hermanos:
- [`EDITOR-MULTITRACK-ARCHITECTURE.md`](EDITOR-MULTITRACK-ARCHITECTURE.md) — arquitectura
  del editor (modelo, operaciones puras, compilación preview/export, persistencia).
- [`EDITOR-ROADMAP.md`](EDITOR-ROADMAP.md) — roadmap por bloques; este documento detalla
  el Bloque 1.
- [`SUBTITLES-TITLES-PRO.md`](SUBTITLES-TITLES-PRO.md) — Bloque 2 (ya en `main`), de donde
  se heredan patrones (operaciones puras, espejos de tipos, subida de ficheros).

---

## 1. Principio rector y restricciones (vinculantes)

El Bloque 1 amplía la **interacción** del editor. Todo lo que se añade respeta las
invariantes de la plataforma:

- **"Lo que ves es lo que exportas".** Cada función nueva se refleja en el preview
  (Remotion Player) y se conserva en el export (Remotion headless + `AudioMixer` a −14
  LUFS). Nada decorativo; cada control es real y conectado al backend o a una función pura
  del frontend testeable.
- **Operaciones puras.** La lógica de edición se añade como funciones
  `op(edit_project, params) -> edit_project` en `backend/app/engines/editor/operations.py`
  registradas en `OPERATIONS`, inmutables, testeables sin ffmpeg/Chromium (patrón del
  Bloque 0/2).
- **Compilador único.** `compile_remotion_props` y `compile_audio_tracks`
  (`backend/app/engines/editor/compile.py`) siguen siendo la ÚNICA fuente de `inputProps`
  (preview+export) y de `MixTrack`.
- **Regresión.** Un `EditProject` previo (sin los campos nuevos) debe producir
  EXACTAMENTE el mismo `inputProps`/`MixTrack` y el mismo render que antes del Bloque 1.
  Esto se garantiza con **defaults neutros** en el modelo y **emisión condicional** en el
  compilador (patrón ya usado para `safeTop`/`titleClips`/karaoke en el Bloque 2).
- **Espejos sincronizados.** Hay que mantener en paridad:
  - DOS espejos de la composición: `remotion/src/EditComposition.tsx` y
    `frontend/components/remotion/EditComposition.tsx`.
  - TRES espejos de tipos TS: `remotion/src/types.ts`, `frontend/lib/editor/types.ts` y
    `frontend/lib/editor/compose.ts` (`compileEditInputProps`, espejo del compilador
    Python).
- **Vía de pago intacta.** El editor edita media ya producida; no toca el registry de
  proveedores (OpenAI/ElevenLabs/Stability/veo/runway/kling queda conmutable a futuro).
- **Sin migraciones de esquema.** La persistencia sigue sobre `Timeline` + `Version`
  (documento JSON versionado). Las secuencias múltiples (§7) se modelan DENTRO de ese
  mecanismo, sin tablas nuevas.

---

## 2. Qué existe hoy y qué hay que tocar

| Pieza | Ruta | Estado para el Bloque 1 |
|-------|------|-------------------------|
| `EditTrack` | `backend/app/engines/editor/model.py` | Ya tiene `muted`, `locked`, `volume`. **Falta `hidden`.** `locked` existe pero NO lo consume ninguna operación/compilador aún. |
| `EditClip` | `model.py` | Añadir `speed` (control de velocidad) y `freeze` (congelar fotograma). Defaults neutros. |
| `EditMeta`/`EditProject` | `model.py` | Añadir `markers` (lista tiempo+etiqueta) y soporte de secuencias múltiples (§7). |
| Operaciones puras | `operations.py` (`OPERATIONS`) | Añadir operaciones nuevas (lock/hide/mute pista, ripple delete, marcadores, detach, batch, copiar atributos, speed/freeze, importar medios → crear clip, secuencias). |
| Compilador | `compile.py` | Honrar `hidden`/`locked` (visual), compilar `speed`/`freeze` a `inputProps` y a filtros de audio; emisión condicional para regresión. |
| `compose_edit` | `backend/app/engines/render/engine.py` | Método hermano de export; NO cambia su estructura. El audio de clips con `speed` se prepara vía `MixTrack.prep` (`atempo`). |
| Subida de media | `backend/app/api/generation.py` (`upload_scene_image`, `upload_overlay_image`) | Solo imagen. **Generalizar** a audio/vídeo (§6) + crear clip. |
| Router editor | `backend/app/api/editor.py` | Añadir endpoint de subida de medios del editor (§6) y, si procede, selección de secuencia activa (§7). |
| Espejos Remotion/TS | `remotion/src/{EditComposition,types}.tsx/.ts`, `frontend/components/remotion/EditComposition.tsx`, `frontend/lib/editor/{types,compose}.ts` | Ampliar en paridad: speed/freeze de clip, hidden de pista, marcadores en el inspector/timeline. |
| Workspace frontend | `frontend/app/projects/[id]/editor/EditorWorkspace.tsx`, `frontend/lib/editor/{drag,fonts}.ts`, `frontend/lib/api.ts` | Snap, selección múltiple, copiar/pegar atributos, drag&drop de importación, marcadores, selector de secuencia. |

`compile_remotion_props` hoy:
- Para audio honra `track.muted` (omite la pista). **No** honra `locked`/`hidden`.
- Para visual/título/overlay NO consulta `muted`/`locked`/`hidden`.
- `compile_audio_tracks` honra `track.muted`. No honra `hidden` (que es concepto visual).

---

## 3. Cambios en el modelo (`model.py`)

Todos los campos nuevos con **default neutro** para la invariante de regresión.

### 3.1 Propiedades de pista: `hidden`

`EditTrack` ya tiene `muted: bool = False` y `locked: bool = False`. Se añade:

```python
hidden: bool = False   # pista visual oculta en preview y export (no en timeline)
```

Semántica (§4 para el reparto por operación y §5 para el compilador):
- `muted` (ya existe): silencia una pista de AUDIO (ya lo honra el compilador). Se amplía
  su semántica documentada para que, en pistas VISUALES, `muted` no tenga efecto (el
  equivalente visual es `hidden`).
- `hidden` (nuevo): una pista VISUAL (`video`/`image`/`title`/`overlay`) oculta no emite
  clips a `visualClips`/`titleClips`/`overlayClips`/`subtitleClips`. No afecta a su
  presencia en el timeline del frontend (la pista se sigue viendo y editando).
- `locked` (ya existe, sin uso): una pista bloqueada rechaza operaciones que muten sus
  clips (trim/split/move/delete/ripple/batch...) con `EditOperationError`. NO afecta
  preview ni export (una pista bloqueada se sigue renderizando). Es protección de edición.

### 3.2 Clip: control de velocidad (`speed`)

```python
class ClipSpeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate: float = 1.0          # 1.0 = normal; >1 acelera; <1 ralentiza; debe ser > 0
    reverse: bool = False      # invertir (solo video/image; ver §8 riesgos)
```

`EditClip.speed: ClipSpeed | None = None`. Interpretación:
- Afecta clips VISUALES recortables (`video`) y de AUDIO; en `image` `reverse` no aplica y
  `rate` solo escala la duración en timeline (una imagen fija no tiene reproducción).
- La **duración en timeline** (`duration_s`) es la que el usuario ve. La relación con el
  origen recortado es: `duration_s = (source_out_s - source_in_s) / rate`. La operación
  `op_set_speed` recalcula `duration_s` a partir del `rate` y del rango de origen (o, si no
  hay origen recortable, escala la duración directamente).
- Default neutro: `speed=None` ⇔ `rate=1.0, reverse=False` ⇔ sin cambios de compilación.

### 3.3 Clip: congelar fotograma (`freeze`)

```python
class ClipFreeze(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_s: float = 0.0          # instante del ORIGEN (source) que se congela
    hold_s: float = 0.0        # cuánto tiempo se mantiene congelado (en timeline)
```

`EditClip.freeze: ClipFreeze | None = None`. Default neutro `None` (sin congelación).
Semántica: un clip con `freeze` muestra el fotograma del origen en `at_s` durante `hold_s`
segundos (a Remotion se le pasa un frame fijo; ver §5.3). MVP: una congelación por clip
(para intervalos múltiples el usuario hace `split` y congela el fragmento). Para `image`
el freeze es redundante (ya es estático) y la operación lo rechaza con mensaje accionable.

### 3.4 Marcadores (`EditProject.markers`)

```python
class EditMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    t_s: float = 0.0           # instante en la línea de tiempo (>= 0)
    label: str = ""
    color: str = "#38bdf8"     # color de la marca en el timeline del frontend

# en EditProject:
markers: list[EditMarker] = Field(default_factory=list)
```

Default `[]` (regresión: un proyecto sin marcadores valida igual). Los marcadores son
metadatos del proyecto: NO afectan al render. Los consume el **snap** del frontend (§ snap)
como puntos de alineación, además de los bordes de clip.

### 3.5 Secuencias múltiples (§7 detalla la decisión)

Se añade a `EditProject`:

```python
sequence_id: str = "main"        # id de la secuencia que ESTE documento representa
sequence_name: str = "Principal" # etiqueta legible
```

Defaults neutros: un `EditProject` previo pasa a ser la secuencia `main`/`Principal` sin
cambiar su render. El catálogo de secuencias de un proyecto se deriva de las filas
`Timeline` de edición (§7), no se añade una lista anidada dentro del documento.

### 3.6 Invariante de regresión del modelo

Test obligatorio: un `EditProject` serializado del Bloque 2 (sin `hidden`/`speed`/`freeze`/
`markers`/`sequence_id`) **valida** con el modelo nuevo y, tras `model_dump`, produce el
mismo `inputProps` que antes. `extra='forbid'` se mantiene en todos los submodelos.

---

## 4. Operaciones puras nuevas (`operations.py`)

Todas siguen el patrón existente (`_clone`, `_require_*`, `EditOperationError`, registro en
`OPERATIONS`). Validan `locked` en las que mutan clips (§3.1).

### 4.1 Propiedades de pista

- `op_set_track_flags(track_id, {locked?, hidden?, muted?})` — fija una o varias banderas
  de una pista. Valida que `muted` solo tenga efecto documentado en audio y `hidden` en
  visual (no rechaza, pero la operación es un no-op semántico fuera de su dominio). Una
  sola operación cubre lock/hide/mute (menos superficie que tres).

### 4.2 Edición ripple (cerrar huecos al eliminar)

- `op_ripple_delete(clip_id)` — elimina el clip y DESPLAZA hacia la izquierda todos los
  clips posteriores de LA MISMA pista por la duración del hueco (de modo que no quede
  hueco). No afecta a otras pistas (ripple por pista; un ripple global de proyecto se
  evalúa como mejora futura). Rechaza si la pista está `locked`.
- `op_close_gap(track_id, t_s?)` — opcional: cierra el hueco a la derecha de `t_s` en una
  pista (mismo mecanismo, sin borrar). Se incluye si cabe en la feature; si no, se difiere.

### 4.3 Selección múltiple (operaciones batch)

El estado de selección vive en el frontend; el backend recibe operaciones batch que
aplican una acción a una LISTA de `clip_ids`:

- `op_batch_delete({clip_ids: [...]})` — elimina varios clips (atómico: si alguno no
  existe o su pista está `locked`, falla entera sin mutar).
- `op_batch_move({clip_ids, delta_s})` — desplaza varios clips por el mismo `delta_s`
  (todos conservan sus offsets relativos). Valida `start_s >= 0` tras el desplazamiento.
- `op_batch_set({clip_ids, ...attrs})` — opcional, aplica atributos comunes (ver 4.4). Se
  implementa reutilizando la operación de copiar atributos.

Decisión: operaciones batch EXPLÍCITAS (no un bucle de `op` suelto por clip) para que el
undo/redo trate el gesto como UN paso de historial (una `Version`).

### 4.4 Copiar/pegar atributos

- `op_copy_attributes({source_clip_id, target_clip_ids, attrs: [...]})` — copia un conjunto
  de atributos (`attrs` ⊆ `{"transform", "gain", "fade", "style", "title", "overlay",
  "transition", "speed"}`) del clip origen a los destino, respetando compatibilidad de tipo
  (no copia `style` de subtítulo a un clip de vídeo; ignora atributos no aplicables o falla
  con mensaje claro, decisión de implementación documentada en el docstring). No copia
  `id`/`start_s`/`duration_s`/`asset_id` (posición y recurso son del destino).

### 4.5 Desvincular audio y vídeo (detach)

- `op_detach_audio({clip_id, target_track_id?})` — para un clip de pista `video` cuyo
  `Asset` tiene pista de audio, crea un clip de AUDIO nuevo en una pista `voice` (o la
  indicada) con el mismo `asset_id`, `start_s`, `duration_s`, `source_in_s/out_s`, `gain`,
  y marca el clip de vídeo como "muteado" a nivel de clip. Como `EditClip` visual no tiene
  flag de mute propio, el detach añade `ClipSpeed`-style neutro pero usa un campo nuevo
  mínimo: `EditClip.mute_source_audio: bool = False` (default neutro) que el compilador usa
  para emitir `muted` en `<OffthreadVideo>` (hoy el export ya pasa `muted` fijo; ver §5.4).
  Si no existe pista `voice` destino, la crea (patrón de `import_subtitles`). Es una
  operación pura (no decodifica el fichero; la extracción real del audio la hace FFmpeg en
  export vía `MixTrack` del nuevo clip).

### 4.6 Control de velocidad

- `op_set_speed({clip_id, rate?, reverse?})` — fija `EditClip.speed`. Valida `rate > 0`.
  Recalcula `duration_s` (§3.2). Rechaza `reverse=True` en clips no-vídeo. Para audio,
  `reverse` también se admite a nivel de modelo pero el MVP del bloque puede **diferir**
  el audio invertido (ver §8).

### 4.7 Congelar fotograma

- `op_set_freeze({clip_id, at_s, hold_s})` — fija `EditClip.freeze`. Valida `at_s` dentro
  del rango del origen y `hold_s > 0`. Rechaza en `image`. Default: pasar `hold_s=0` o
  `freeze=None` limpia la congelación.

### 4.8 Marcadores

- `op_add_marker({t_s, label?, color?})` — añade un `EditMarker` (id determinista).
- `op_remove_marker({marker_id})`.
- `op_set_marker({marker_id, t_s?, label?, color?})`.

### 4.9 Importar medios (crear clip desde un Asset subido)

El endpoint de subida (§6) crea el `Asset`; la colocación en la pista es una operación pura:

- `op_add_media_clip({track_id, asset_id, start_s, duration_s, kind})` — crea un `EditClip`
  del `kind` correcto (video/image/voice/music/sfx) en la pista indicada (valida que el
  `kind` del clip casa con el `kind` de la pista). Reutiliza el patrón de `op_add_overlay`/
  `op_add_title`. Para audio/vídeo con duración de origen conocida fija `source_out_s`.

### 4.10 Secuencias (§7)

- No añade operaciones al documento activo; la gestión de secuencias es a nivel de
  persistencia/endpoints (§7). Se mencionan aquí para completar el inventario.

---

## 5. Compilación (preview + export)

### 5.1 Pistas ocultas/bloqueadas/muteadas

En `compile_remotion_props`, al iterar pistas:
- Si `track.hidden` y la pista es VISUAL (`video`/`image`/`title`/`overlay`/`subtitle`): se
  OMITEN sus clips (no se emiten a los arrays correspondientes).
- Si `track.muted` y la pista es de AUDIO: se omite (comportamiento actual, sin cambios).
- `locked` NO afecta a la compilación (es protección de edición, no de render).

`compile_audio_tracks` mantiene el filtro por `track.muted` (sin cambios). Regresión: una
pista con `hidden=False`/`muted=False` produce el mismo output que hoy.

### 5.2 Velocidad

- **Visual (`video`):** se emite en el clip de `visualClips` un bloque opcional `speed`:
  `{"rate": <r>, "reverse": <bool>}`, SOLO cuando `rate != 1.0` o `reverse` (regresión). La
  composición Remotion aplica `playbackRate={rate}` a `<OffthreadVideo>` y, para `reverse`,
  usa un origen pre-invertido (ver §8: el reverse real lo produce FFmpeg en export; en
  preview se muestra una nota o una aproximación). `durationInFrames` ya refleja la duración
  en timeline (el compilador usa `duration_s`, que la operación ya ajustó por `rate`).
- **Audio:** en `compile_audio_tracks`, `_clip_prep_filters` añade `atempo` para `rate`
  (encadenando factores en [0.5, 2.0] cuando `rate` cae fuera de ese rango, como exige
  FFmpeg) y, si se habilita (ver §8), `areverse` para audio invertido. El `atrim` del
  origen se aplica ANTES del `atempo`.

### 5.3 Freeze (congelar fotograma)

- **Visual:** se emite `freeze: {"atFrame": <frame del origen>, "holdInFrames": <frames>}`
  en el clip de `visualClips` SOLO cuando `freeze` está definido. La composición pinta un
  `<OffthreadVideo>`/`<Img>` fijado al frame `atFrame` durante `holdInFrames` (Remotion:
  `<OffthreadVideo>` con `startFrom`/`endAt` iguales, o un `<Img>` del fotograma extraído).
  Para el MVP, la congelación es un tramo del clip (el usuario la coloca con `split`+freeze
  o el freeze reemplaza la reproducción del clip por el fotograma mantenido).
- **Audio:** el freeze es visual; el audio del tramo congelado se silencia o se mantiene
  según decisión documentada (MVP: se mantiene el audio tal cual, el freeze solo afecta al
  vídeo, que es el caso de uso habitual "congelar imagen sin cortar la voz en off").

### 5.4 Detach de audio

- El clip de vídeo con `mute_source_audio=True` emite `muted: true` en su entrada de
  `visualClips`. En EXPORT el vídeo ya se renderiza mudo globalmente (`muted=True`), así que
  el detach importa sobre todo para el PREVIEW (el `<OffthreadVideo>` del Player deja de
  reproducir su pista de audio y el audio pasa a oírse por el nuevo clip de la pista
  `voice`). En export, el nuevo clip de audio aporta su `MixTrack` como cualquier voz.

### 5.5 Espejo TS del compilador

`frontend/lib/editor/compose.ts::compileEditInputProps` replica EXACTAMENTE la lógica de
emisión condicional (speed/freeze/hidden) para que el preview y el export produzcan el
mismo `inputProps`. Test de paridad: un fixture `EditProject` compilado por Python y por TS
produce el mismo JSON (ya hay precedente de esta comprobación en el Bloque 2).

---

## 6. Generalización de la subida de medios (importar archivos)

Hoy `upload_scene_image` (escena) y `upload_overlay_image` (proyecto) aceptan SOLO imagen.
El drag&drop de importación del editor necesita subir **audio/vídeo/imagen** a nivel de
proyecto y obtener un `Asset` del `kind` correcto.

### 6.1 Endpoint nuevo

`POST /projects/{project_id}/editor/media/upload` (en `backend/app/api/editor.py`, junto a
`import_subtitles`, reutilizando sus patrones de validación):

- Acepta `UploadFile`. Detecta el `kind` por extensión + content-type:
  - imagen → `.png/.jpg/.jpeg/.webp/.gif` (reusa `_ALLOWED_IMAGE_EXTS` de `generation.py`,
    extraído a un módulo común o replicado), `Asset(kind='image')`.
  - vídeo → `.mp4/.mov/.webm/.mkv`, `Asset(kind='video')`.
  - audio → `.mp3/.wav/.m4a/.aac/.ogg/.flac`, mapeado a `kind` según un parámetro `role`
    (`voice`/`music`/`sfx`; default `music`) porque `Asset.kind` ya soporta esos valores.
- Valida tamaño (`_MAX_UPLOAD_BYTES`, hoy 15 MB; se puede subir para vídeo con una constante
  propia documentada). Valida contenido: imagen con Pillow (patrón actual); audio/vídeo con
  **ffprobe** (`AIV_FFPROBE_BIN`) para confirmar que tiene streams válidos y leer
  `duration_s` (que alimenta `source_out_s` del clip). Si ffprobe no está disponible, se
  rechaza con mensaje accionable (no se finge validación).
- Guarda el fichero bajo el workspace del proyecto (`media/uploads/`, bajo `AIV_MEDIA_ROOT`,
  servible por `GET /assets/{id}/download` con Range, que el Player ya usa).
- Devuelve `AssetRead` (id + kind + meta con `duration_s`/dimensiones).

Decisión: NO se crea el clip en el mismo endpoint. El frontend, tras subir, llama a
`op_add_media_clip` (§4.9) con la pista y el `start_s` donde el usuario soltó el archivo.
Así la subida (I/O) y la colocación (pura) quedan separadas y testeables por separado.

### 6.2 Extracción de constantes

Las listas `_ALLOWED_*`/`_MAX_UPLOAD_BYTES`/`_safe_stem` de `generation.py` se extraen a un
helper compartido (p. ej. `backend/app/api/uploads.py` o `app/storage/uploads.py`) para no
duplicarlas entre `generation.py` y `editor.py`. Refactor sin cambio de comportamiento
(los tests de `upload_scene_image`/`upload_overlay_image` existentes deben seguir pasando).

---

## 7. Varias secuencias por proyecto

**Problema.** El usuario quiere varias variantes del vídeo (p. ej. versión 16:9 y versión
9:16, o un corte largo y uno corto) DENTRO de un mismo proyecto.

**Modelo elegido (sin migración).** Una "secuencia" es un `EditProject` independiente
identificado por `sequence_id` (§3.5). La persistencia (`store.py`) ya guarda cada
`EditProject` como filas `Timeline(kind='edit_project')` + `Version`. Se extiende el
puntero para que sea **por secuencia**:

- Cada fila `Timeline` de edición lleva en su JSON `sequence_id`. El "activo" deja de ser
  "la fila de mayor versión" y pasa a ser "la fila de mayor versión CON ese `sequence_id`".
- `Version` (undo/redo) se segmenta por secuencia: `entity_type='edit_project'` se mantiene,
  y el cursor/`_max` se calculan sobre las versiones de esa `sequence_id` (se añade
  `sequence_id` al snapshot). Así cada secuencia tiene su propio historial de undo/redo.
- La secuencia por defecto es `main` (compatibilidad: documentos previos sin `sequence_id`
  se tratan como `main`).

**Endpoints nuevos** (`backend/app/api/editor.py`):
- `GET /projects/{id}/editor/sequences` — lista las secuencias (`sequence_id`,
  `sequence_name`, duración, versión activa).
- `POST /projects/{id}/editor/sequences` — crea una secuencia nueva (opcionalmente
  **duplicando** la activa como punto de partida, o derivándola del `Timeline` como hace
  `open`). Devuelve el `EditProject` de la secuencia nueva.
- `GET`/`PUT`/`op`/`undo`/`redo`/`export` aceptan un parámetro `sequence` (query o body;
  default `main`) para operar sobre la secuencia elegida. Sin el parámetro, el
  comportamiento es idéntico al actual (regresión).

**Riesgo / alcance honesto.** Esto toca `store.py` (la parte más delicada del editor:
cursor de undo/redo). Se hace en una feature AISLADA, al final del bloque, con tests
dedicados de que (a) sin `sequence_id` todo el undo/redo actual sigue igual (regresión), y
(b) dos secuencias mantienen historiales independientes. Si el riesgo de regresión del
historial resulta alto durante la implementación, la feature se marca `blocked` con detalle
en vez de arriesgar el undo/redo ya probado.

---

## 8. Riesgos y notas honestas

- **Invertir vídeo (`reverse`) es caro.** FFmpeg `reverse`/`areverse` cargan todo el clip en
  memoria y son lentos; en clips largos puede ser inviable en el PC del usuario. **Decisión
  de alcance:** en el MVP del Bloque 1 se modela `reverse` en `ClipSpeed` y se compila el
  camino de EXPORT con FFmpeg (`reverse` para vídeo, `areverse` para audio) PERO:
  - El **preview** no reproduce vídeo invertido en tiempo real de forma fiel (Remotion no
    reproduce al revés sin un origen pre-invertido). El preview muestra el clip normal con
    un indicador "invertido (se aplica en export)" — una divergencia controlada y
    documentada, no un control decorativo.
  - Si el reverse real no cabe en la feature sin comprometer tiempos de render, se **difiere
    a una iteración posterior** dejando la operación `op_set_speed` aceptando `reverse` pero
    el compilador marcándolo como no soportado-aún con `EditOperationError`/nota (nunca
    fingir). Esta decisión se registra en `findings` de la feature.
- **`atempo` fuera de [0.5, 2.0].** FFmpeg exige encadenar varios `atempo`. El compilador
  genera la cadena correcta; se cubre con test puro de la lista de filtros.
- **Snap es UX de frontend.** El ajuste magnético NO cambia el backend: es una función pura
  `snapTime(t, candidates, thresholdPx, pxPerSecond)` en el frontend que, al soltar un clip,
  redondea el tiempo al borde/marcador más cercano dentro de un umbral. La operación
  persistida (`move`/`set_timing`/`batch_move`) recibe el tiempo YA ajustado. Es testeable
  con Jest/unitario puro + smoke manual; no requiere render.
- **Freeze y audio.** El freeze afecta solo al vídeo en el MVP (el audio sigue). Congelar
  también el audio se puede añadir después si el usuario lo pide.
- **Múltiples secuencias y undo/redo.** Ver §7: es el cambio con más riesgo de regresión;
  aislado y con tests de regresión del historial.
- **Verificación visual.** Snap, selección múltiple, copiar/pegar y drag&drop de
  importación son interacciones de navegador: se verifican con funciones puras testeadas +
  `build/lint/typecheck` + smoke manual documentado. El render de speed/freeze se verifica
  en export con `ffprobe` (duración/streams coherentes) cuando el render corre; si no, skip
  con razón.

---

## 9. Estrategia de pruebas (alineada con el sandbox)

- **Operaciones puras** (`operations.py`): tests `pytest` deterministas sin render para
  cada operación nueva (lock/hide/mute, ripple, batch, copy_attributes, detach, speed,
  freeze, markers, add_media_clip). Incluye casos de error (`locked`, tipo incompatible,
  rangos inválidos) que deben lanzar `EditOperationError`.
- **Compilador** (`compile.py` y espejo `compose.ts`): tests de que `hidden`/`speed`/
  `freeze` se emiten SOLO cuando difieren del default (regresión) y con la forma correcta;
  test de paridad Python↔TS sobre un fixture.
- **Subida de medios** (`editor.py`): tests con un WAV/MP4 real mínimo (los fixtures del
  repo ya incluyen media para `test_render_engine`) validados con `ffprobe`; casos de
  rechazo (vacío, demasiado grande, no media). Skip con razón si `ffprobe` no está.
- **Export con `ffprobe`** (`compose_edit`): un `EditProject` con un clip a `rate=2.0` y un
  clip con `freeze` produce un MP4 con duración coherente; skip con razón si el render
  Remotion no corre (en este sandbox SÍ corre).
- **Secuencias** (`store.py`): regresión del undo/redo de la secuencia `main` + historiales
  independientes entre dos secuencias.
- **Frontend/remotion**: `npm run build && npm run lint && npm run typecheck` (frontend) y
  `npm run typecheck` (remotion). Snap/selección/copiar/drag&drop con pruebas unitarias de
  las funciones puras si hay runner JS; si no, smoke manual documentado.

Comandos de referencia:
- Backend: `cd backend && . .venv/bin/activate && AIV_FFMPEG_BIN=/projects/sandbox/.localbin/ffmpeg AIV_FFPROBE_BIN=/projects/sandbox/.localbin/ffprobe pytest -q`
- Frontend: `cd frontend && npm run build && npm run lint && npm run typecheck`
- Remotion: `cd remotion && npm run typecheck`

---

## 10. Desglose en features

Ver `.agents/tasks/task-editor-avanzado/` para el detalle con `acceptance_criteria` y
`verification`. Resumen del orden (dependencias primero):

1. **FEAT-001 — Baseline verde** (chore). Confirmar suite en `main` (esperado 322 passed /
   2 skipped con ffmpeg) + build/lint/typecheck frontend/remotion antes de ampliar.
2. **FEAT-002 — Propiedades de pista (lock/hide/mute) + ripple delete** (feat). Modelo
   `hidden`, `op_set_track_flags`, `op_ripple_delete`, compilador honra `hidden`, espejos.
3. **FEAT-003 — Marcadores + snap** (feat). `EditMarker` en modelo, ops de marcador,
   función pura de snap en frontend, timeline muestra marcadores.
4. **FEAT-004 — Selección múltiple + copiar/pegar atributos** (feat). Ops batch,
   `op_copy_attributes`, estado de selección y UI en el workspace.
5. **FEAT-005 — Desvincular audio/vídeo (detach)** (feat). `mute_source_audio`,
   `op_detach_audio`, compilador emite `muted` en el preview.
6. **FEAT-006 — Velocidad + congelar fotograma** (feat). `ClipSpeed`/`ClipFreeze`,
   `op_set_speed`/`op_set_freeze`, compilación a `inputProps` + `atempo`, render ffprobe.
   Nota honesta de reverse (§8).
7. **FEAT-007 — Importar medios (drag&drop de archivos)** (feat). Endpoint de subida
   generalizado a audio/vídeo/imagen + `op_add_media_clip` + drag&drop de importación en el
   frontend.
8. **FEAT-008 — Varias secuencias por proyecto** (feat, mayor riesgo, aislada al final).
   `sequence_id`/`sequence_name`, persistencia por secuencia en `store.py`, endpoints de
   secuencias, selector en el frontend, tests de regresión del historial.

Las features 2–7 son en buena medida independientes entre sí (tocan operaciones/campos
distintos); se ordenan por afinidad temática y para que el frontend reutilice piezas
(p. ej. el snap de FEAT-003 lo usa el drag de FEAT-007). FEAT-008 va al final por su riesgo
sobre el undo/redo.
