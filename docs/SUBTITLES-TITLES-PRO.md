# Subtítulos y Títulos Pro (Bloque 2) — Diseño

> Estado: **PLAN / diseño**. Describe cómo se implementa el **Bloque 2** del
> `docs/EDITOR-ROADMAP.md` (Subtítulos y Títulos Pro, para cortometrajes) SOBRE el editor
> multipista `EditProject` ya consolidado en `main` (Bloque 0, PR #13). No se reescribe
> nada: se **amplía** el modelo, las operaciones puras, el compilador, la API y las DOS
> composiciones `EditComposition` (espejo backend/frontend), conservando la paridad
> "lo que ves = lo que exportas" y la vía de pago conmutable intacta.

Documentos hermanos: [`EDITOR-MULTITRACK-ARCHITECTURE.md`](EDITOR-MULTITRACK-ARCHITECTURE.md)
(arquitectura del editor), [`EDITOR-ROADMAP.md`](EDITOR-ROADMAP.md) (bloques y orden).

---

## 0. Principios (heredados, no negociables)

- **Paridad preview/export.** El preview (Remotion Player) y el export (Remotion headless
  + `AudioMixer`/FFmpeg) consumen el MISMO `EditProject` compilado por el MISMO compilador
  (`compile_remotion_props`), reflejado en las dos `EditComposition` (que deben quedar
  byte-equivalentes en comportamiento: `remotion/src/EditComposition.tsx` y
  `frontend/components/remotion/EditComposition.tsx`).
- **Todo real y conectado.** Cada control muta el `EditProject` persistido en backend
  (tabla `Timeline`, versionado en `Version`) y se ve en el preview y se conserva en el
  export. Nada decorativo.
- **No romper nada.** No se elimina ni renombra ningún módulo, tipo de pista, operación ni
  endpoint existente. La composición `Timeline` de generación no se toca. La vía de pago
  (OpenAI/ElevenLabs/Stability/veo/runway/kling) sigue conmutable; el editor no la toca.
- **Reutilizar lo que existe.** Whisper/`faster-whisper`, `palabras.json`
  (`[{text,startMs,endMs}]`), `backend/app/engines/voice/subtitles.py` (cues/SRT), el
  estilo de subtítulo por clip (`SubtitleStyle`), el patrón de subida de imagen
  (`POST /scenes/{id}/image/upload`) y `GET /assets/{id}/download` (con Range).

---

## 1. Qué existe hoy y se reutiliza (base del Bloque 2)

| Pieza | Ruta | Rol que cumple para el Bloque 2 |
|---|---|---|
| `SubtitleStyle` (Pydantic) | `backend/app/engines/editor/model.py` | Estilo por clip de subtítulo: `font_family, font_size, color, bg_color, bold, outline, position, x, y, preset(classic\|tiktok)`. **Se amplía** (contorno/sombra/fondo finos, presets nuevos). |
| `op_set_style`, `op_set_text`, `op_set_timing` | `backend/app/engines/editor/operations.py` | Operaciones puras de subtítulo ya existentes. **Se amplían** campos de estilo y se añaden operaciones nuevas. |
| `compile_remotion_props` / `compile_audio_tracks` | `backend/app/engines/editor/compile.py` | Compila `EditProject` → `inputProps` (preview+export) y `MixTrack`. **Se amplía** para karaoke/títulos/overlays. |
| `EditComposition` (x2 espejos) | `remotion/src/EditComposition.tsx`, `frontend/components/remotion/EditComposition.tsx` | Render de clips visuales + subtítulos (Classic/TikTok) + audio. **Se amplía** con karaoke, títulos y overlays. |
| `palabras.json` + `Asset(kind='srt').meta['words_json']` | `backend/app/engines/voice/engine.py`, `subtitles.py` | Timings por palabra (`startMs/endMs`) del ASR real o fallback text-derived. **Fuente del karaoke.** |
| `subtitles.py` (cues/SRT) | `backend/app/engines/voice/subtitles.py` | `Cue`, `write_srt`, `cues_*`, `words_payload`. **Se amplía** con VTT y ASS (parsers + serializadores). |
| Subida de imagen | `backend/app/api/generation.py::upload_scene_image` | Patrón de `UploadFile` validado (tipo/tamaño/Pillow) dentro del workspace. **Se generaliza** a subida de overlays/logos a nivel de proyecto. |
| `GET /assets/{id}/download` (Range) | `backend/app/api/assets.py` | Sirve ficheros al Player. **Reutilizado** tal cual para overlays/logos. |
| Convert timeline→edit | `backend/app/engines/editor/convert.py` | Deriva pistas al abrir el editor. **Se amplía** para copiar `words`/preset de subtítulo y crear pistas vacías de título/overlay. |

### 1.1 Cadena del karaoke (la pieza clave reutilizada)

El ASR ya produce `palabras.json` con `[{text, startMs, endMs}]`, referenciado desde
`Asset(kind='srt').meta['words_json']`. Hoy `convert.timeline_to_edit_project` crea clips
de subtítulo por **cue** (grupo de palabras) sin los timings por palabra. Para el karaoke
NO se re-ejecuta el ASR: se **adjunta** a cada clip de subtítulo la lista de palabras con
sus tiempos relativos al clip, leída de `palabras.json`. Si no hay `palabras.json` (p. ej.
subtítulo creado a mano), se cae a un reparto determinista por palabra
(`cues_per_word`/reparto uniforme), etiquetado como aproximado, nunca simulado como ASR.

---

## 2. Modelo de datos (ampliación de `EditProject`)

Se **añaden** tipos de pista y campos de clip; no se modifica la semántica de los
existentes. Todo con `model_config = ConfigDict(extra="forbid")` como el resto del modelo
(el frontend `frontend/lib/editor/types.ts` replica cada cambio).

### 2.1 Nuevos `TrackKind`

```
TrackKind = Literal["video","image","voice","music","sfx","subtitle",
                    "title","overlay"]          # <- nuevos
VISUAL_RENDER_KINDS  # video,image,title,overlay  (lo que pinta EditComposition)
```

- **`title`**: texto animado (intro, lower-third, créditos). Clip con `text` + `title`
  (config de estilo + animación). No referencia `Asset` (es texto puro) salvo fuente.
- **`overlay`**: capa gráfica (imagen/logo/marca de agua/emoji-gráfico). Clip con
  `asset_id` (imagen subida) y `transform`/`overlay` (opacidad, anchor, tamaño relativo).

Los emojis de texto se soportan como `title`/subtítulo normales (son caracteres Unicode);
los emojis **gráficos** (PNG/SVG) y logos entran como `overlay` con `Asset(kind='image')`.

### 2.2 `SubtitleStyle` ampliado (contornos/sombras/fondos + karaoke)

Campos NUEVOS (todos con default retrocompatible; el documento viejo sigue validando):

```python
class SubtitleStyle(BaseModel):
    # ... campos actuales (font_family, font_size, color, bg_color, bold, outline,
    #     position, x, y, preset) ...
    # Contorno fino:
    outline_color: str = "#000000"
    outline_width: float = 0.0          # px; 0 = usar el textShadow heredado (compat)
    # Sombra:
    shadow: bool = False
    shadow_color: str = "#000000"
    shadow_blur: float = 0.0
    shadow_x: float = 0.0
    shadow_y: float = 0.0
    # Fondo (caja):
    bg_opacity: float = 1.0             # 0..1 sobre bg_color
    bg_padding: float = 0.2             # em
    bg_radius: float = 8.0              # px
    # Karaoke (resaltado por palabra):
    karaoke: bool = False
    highlight_color: str = "#FFE34D"    # color de la palabra activa
    highlight_scale: float = 1.0        # 1.0 = sin zoom; >1 agranda la palabra activa
    # presets nuevos (ver §6): preset ∈ classic|tiktok|reels|shorts|cinema|clean
```

`preset` pasa a `Literal["classic","tiktok","reels","shorts","cinema","clean"]`. Los
presets nuevos son **datos** (tabla de estilos base, §6), no ramas de código nuevas en el
render salvo matices de posición/zona segura.

### 2.3 Palabras por clip de subtítulo (karaoke)

Se añade al `EditClip` un campo opcional de palabras con tiempos **relativos al clip**:

```python
class SubtitleWord(BaseModel):
    text: str
    start_s: float      # relativo al inicio del clip (0..duration_s)
    end_s: float

class EditClip(BaseModel):
    # ...
    words: list[SubtitleWord] = Field(default_factory=list)   # vacío = sin karaoke por palabra
```

`convert.py` rellena `words` desde `palabras.json` (restando el offset del clip). El
compilador emite `words` a `inputProps` solo para clips de subtítulo con `karaoke=True`.

### 2.4 Clip de título (`TitleStyle` + `TitleAnimation`)

```python
class TitleAnimation(BaseModel):
    type: Literal["none","fade","slide_up","slide_down","typewriter","scale_in",
                  "credits_roll"] = "fade"
    duration_s: float = 0.8            # duración de la animación de entrada
    direction: Literal["in","out","both"] = "both"

class TitleStyle(BaseModel):
    # Reutiliza los mismos campos de texto que SubtitleStyle (fuente/contorno/sombra/
    # fondo/color) + layout de título:
    font_family: str = "Inter, sans-serif"
    font_size: int = 96
    color: str = "#FFFFFF"
    bold: bool = True
    align: Literal["left","center","right"] = "center"
    # posición en el frame (fracción 0..1 del ancho/alto, para drag&drop):
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    # (contorno/sombra/fondo: mismos campos que SubtitleStyle, factorizados en un mixin)
    kind: Literal["intro","lower_third","credits","plain"] = "plain"

class EditClip(BaseModel):
    # ...
    title: TitleStyle | None = None
    title_animation: TitleAnimation | None = None
```

Un clip de título válido: `kind == "title"`, `text` no vacío, `title` presente.

### 2.5 Clip de overlay (`OverlayStyle`)

```python
class OverlayStyle(BaseModel):
    opacity: float = 1.0               # 0..1
    anchor_x: float = 0.5              # posición (fracción del frame) para drag&drop
    anchor_y: float = 0.5
    width_frac: float = 0.2            # ancho relativo al frame (alto por aspecto del asset)
    rotation: float = 0.0
    # marca de agua: repetición/mosaico opcional
    tile: bool = False

class EditClip(BaseModel):
    # ...
    overlay: OverlayStyle | None = None
```

Un clip de overlay válido: `kind == "overlay"`, `asset_id` resoluble a una imagen,
`overlay` presente. El logo/marca de agua es un overlay que abarca toda la duración del
proyecto (o un rango).

> **Factorización:** los campos de contorno/sombra/fondo/fuente se definen una vez en un
> mixin Pydantic (`TextDecoration`) y se componen en `SubtitleStyle` y `TitleStyle` para no
> duplicar validación. El frontend hace lo propio en `types.ts`.

### 2.6 Validación e invariantes

- `title`/`overlay` solo en pistas `title`/`overlay` (validador `_clips_match_track` ya
  comprueba `clip.kind == track.kind`; se extiende la enum).
- `karaoke=True` sin `words` → el render cae al resaltado por tiempo uniforme (no falla).
- `OverlayStyle.width_frac`, `opacity`, `anchor_*` en rangos `0..1` (validadores).
- Retrocompatibilidad: un `EditProject` previo (sin los campos nuevos) valida igual
  porque todos los nuevos campos tienen default.

---

## 3. Operaciones puras (ampliación de `operations.py`)

Nuevas funciones `op(edit_project, params) -> edit_project` (inmutables, testeables sin
render), registradas en `OPERATIONS`:

| Operación | Params | Efecto |
|---|---|---|
| `set_style` (**ampliada**) | campos nuevos de §2.2 (`outline_color`, `outline_width`, `shadow*`, `bg_*`, `karaoke`, `highlight_*`, `preset` extendido) | Fija estilo de subtítulo, validando enums/rangos. |
| `apply_subtitle_preset` | `clip_id`, `preset` | Aplica un preset de `§6` (set de valores de estilo) a un clip. |
| `apply_subtitle_preset_all` | `preset`, opcional `track_id` | Aplica el preset a todos los clips de la pista de subtítulos. |
| `set_karaoke` | `clip_id`, `enabled`, opcional `highlight_color/scale` | Activa/configura karaoke en un clip. |
| `add_title` | `track_id`, `text`, `start_s`, `duration_s`, opcional `title`/`animation` | Crea un clip de título. |
| `set_title_style` | `clip_id`, campos de `TitleStyle` | Edita estilo del título. |
| `set_title_animation` | `clip_id`, `type`, `duration_s`, `direction` | Edita la animación del título. |
| `add_overlay` | `track_id`, `asset_id`, `start_s`, `duration_s`, opcional `overlay` | Crea un clip de overlay (imagen/logo/emoji gráfico). |
| `set_overlay_style` | `clip_id`, campos de `OverlayStyle` | Edita opacidad/tamaño/rotación/tile. |
| `set_position` | `clip_id`, `anchor_x`, `anchor_y` | **Mapea el drag&drop**: fija la posición del título/overlay (o `style.x/y` de subtítulo) desde el preview. |
| `add_track` | `kind`, `name`, opcional `order` | Crea una pista `title`/`overlay` (para poder añadir clips a una pista nueva). |

Todas validan referencias/invariantes y lanzan `EditOperationError` con mensaje
accionable (igual que las existentes). `set_position` es la operación a la que el
frontend traduce el arrastrar-y-soltar sobre el Player (ver §8).

---

## 4. Import/Export SRT, VTT, ASS

### 4.1 Módulo de formatos

Nuevo `backend/app/engines/editor/subtitle_formats.py` (lógica pura, 100% testeable sin
ffmpeg), con parsers y serializadores sobre la lista de `Cue` (reutiliza `Cue` y `_fmt`/
estructura de `backend/app/engines/voice/subtitles.py`, que YA escribe SRT):

```python
# SRT
parse_srt(text: str) -> list[Cue]
write_srt(cues, path)                 # ya existe en voice/subtitles.py (se reutiliza)
# WebVTT
parse_vtt(text: str) -> list[Cue]
dump_vtt(cues: list[Cue]) -> str      # cabecera "WEBVTT", timestamps HH:MM:SS.mmm
# ASS/SSA (Advanced SubStation Alpha)
parse_ass(text: str) -> list[Cue]     # lee [Events] Dialogue:, Start/End, Text
dump_ass(cues, style: SubtitleStyle | None) -> str   # [Script Info]+[V4+ Styles]+[Events]
```

Notas de diseño:
- **VTT**: idéntico a SRT salvo cabecera `WEBVTT` y `.` en los milisegundos (en vez de
  `,`). El parser tolera cues sin índice numérico y notas `NOTE`.
- **ASS**: se emite un `[V4+ Styles]` derivado del `SubtitleStyle` del clip (fuente,
  tamaño, color primario `&HAABBGGRR`, contorno/sombra, alineación por `position`). El
  parser extrae `Dialogue` (Start/End/Text), ignorando overrides `{\...}` complejos en el
  MVP (se documenta como limitación; el texto plano se conserva). La conversión de color
  hex `#RRGGBB` ↔ ASS `&HBBGGRR` vive en helpers puros con tests.
- Las tres rutas convierten **desde/hacia `list[Cue]`**; un adaptador
  `cues_to_subtitle_clips` / `subtitle_clips_to_cues` traduce entre `Cue` y los clips de
  la pista `subtitle` del `EditProject` (tiempos absolutos ↔ cues).

### 4.2 Endpoints (en `backend/app/api/editor.py`)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/projects/{id}/editor/subtitles/export?format=srt\|vtt\|ass` | Serializa la pista `subtitle` activa del `EditProject` y devuelve el fichero (`text/plain`/`text/vtt`), nombre `subtitles.<ext>`. |
| `POST` | `/projects/{id}/editor/subtitles/import` | Sube un `.srt/.vtt/.ass` (`UploadFile`), lo parsea a cues, **reemplaza** (o añade, por flag `mode=replace\|append`) la pista `subtitle` del `EditProject`, crea `Version`. Devuelve el `EditProject`. |

El import valida formato por extensión + contenido (como la subida de imagen). También se
guarda el fichero importado como `Asset(kind='srt')` para trazabilidad (reutiliza el
patrón de `Asset`). El export por formato es coherente con la pista editada (si el usuario
editó tiempos/texto en el editor, el SRT/VTT/ASS exportado lo refleja: paridad editor↔
subtítulos).

---

## 5. Render en `EditComposition` (preview + export, DOS espejos)

Toda ampliación de render se hace **idéntica** en los dos ficheros:
`remotion/src/EditComposition.tsx` (headless/export) y
`frontend/components/remotion/EditComposition.tsx` (Player/preview). Los tipos espejo
viven en `remotion/src/types.ts` y `frontend/lib/editor/compose.ts` respectivamente.

### 5.1 Karaoke (resaltado por palabra)

El `SubtitleOverlay` recibe `words` (relativos al clip) y, si `karaoke=true`, renderiza el
texto como `<span>` por palabra; usa `useCurrentFrame()` (ya importado) para decidir qué
palabra está activa (`startFrame <= frame < endFrame`) y le aplica `highlight_color` y
`highlight_scale`. Como ya estamos dentro de la `<Sequence>` del clip, `frame` es local al
clip, igual que hace hoy `VisualClip` con las transiciones fade. Sin `words`, cae al render
actual (texto completo). **Determinista** → verificable por snapshot de props + smoke
visual.

### 5.2 Contornos/sombras/fondos

`SubtitleOverlay` (y el nuevo `TitleOverlay`) construyen `textShadow`/`WebkitTextStroke`/
`backgroundColor` desde los campos nuevos de estilo. Se conserva el comportamiento actual
cuando los campos nuevos están en su default (el `blackStroke` TikTok y el shadow classic
siguen saliendo igual si `outline_width==0`). Esto garantiza que los `EditProject`
anteriores se ven idénticos.

### 5.3 Títulos animados y créditos (`TitleOverlay`)

Nuevo componente `TitleOverlay` (en ambos espejos) que pinta `clip.text` con `TitleStyle`
en la posición `anchor_x/anchor_y` (fracción del frame) y anima con `interpolate`/`spring`
de Remotion según `title_animation.type`:
- `fade`/`slide_up`/`slide_down`/`scale_in`: entrada/salida en los primeros/últimos frames
  del clip (patrón idéntico al fade de `VisualClip`).
- `typewriter`: substring del texto en función del frame.
- `credits_roll`: desplazamiento vertical continuo (créditos finales) a lo largo de la
  duración del clip.
`lower_third` es un `TitleStyle.kind` con layout preposicionado (banda inferior
izquierda); es composición/posición, no un render aparte.

### 5.4 Overlays gráficos / logos / marcas de agua (`OverlayClip`)

Nuevo `OverlayClip` que pinta `<Img src=...>` (reutiliza `resolveSrc`) con `opacity`,
`width_frac` (ancho = `width_frac * composition.width`, alto por aspecto), posición
`anchor_x/anchor_y` y `rotation`. `tile=true` repite el patrón (marca de agua en mosaico).
Las pistas `title`/`overlay` se dibujan **por encima** de las visuales y por debajo/encima
de los subtítulos según `track.order` (se respeta el orden de pista existente).

### 5.5 Compilador: nuevas salidas en `inputProps`

`compile_remotion_props` emite dos listas nuevas, ordenadas por `from`:
`titleClips` (con `text`, `style`, `animation`, `durationInFrames`) y `overlayClips`
(con `src`, `overlay`, `durationInFrames`); y añade `words` a los `subtitleClips` con
karaoke. `compile_audio_tracks` **no cambia** (títulos/overlays no tienen audio). Los
espejos de tipos (`remotion/src/types.ts`, `frontend/lib/editor/compose.ts`) se amplían en
paralelo. El export headless sigue `muted=true`: nada de audio nuevo, el `AudioMixer`
−14 LUFS intacto.

### 5.6 Registro de composición

No se añade una composición Remotion nueva: se amplía la existente `Edit` en `Root.tsx`
(mismos `calculateMetadata`). `compose_edit` y `render.mjs` NO cambian (ya pasan
`--composition Edit` y el `inputProps` completo). Esto mantiene el export probado.

---

## 6. Plantillas / presets (incl. TikTok/Reels/Shorts)

Nuevo `backend/app/engines/editor/presets.py` (datos puros): un diccionario
`SUBTITLE_PRESETS: dict[str, dict]` con los valores de `SubtitleStyle` por preset, y
`FORMAT_PRESETS` con resoluciones/zonas seguras verticales:

| Preset | Formato | Rasgos |
|---|---|---|
| `classic` | 16:9 | Caja negra translúcida, texto blanco, abajo (comportamiento actual). |
| `tiktok` | 9:16 | Mayúsculas, contorno negro grueso, sin caja, subido sobre la zona de UI. |
| `reels` | 9:16 | Sans bold, caja semitransparente redondeada, centrado-bajo, zona segura Instagram. |
| `shorts` | 9:16 | Alto contraste, contorno + sombra, posición media-baja, zona segura YouTube. |
| `cinema` | 16:9 (2.39 letterbox opcional) | Serif/Inter, sin caja, sombra suave, subtítulo de cortometraje. |
| `clean` | cualquiera | Sans, sin caja ni contorno, sombra mínima. |

- Los presets de formato 9:16 definen `width/height` (1080×1920) y **zonas seguras**
  (márgenes superior/inferior en fracción) que el frontend usa para encuadrar títulos/
  subtítulos sin que los tape la UI de la red social; el render respeta esos márgenes al
  posicionar.
- `op_apply_subtitle_preset`/`_all` aplican un preset a clip(s). El frontend ofrece
  selector de preset en el inspector y un selector de **formato** (9:16/16:9/1:1) que
  cambia `meta.width/height/aspect_ratio` vía `PUT` (o una op `set_format`).
- Presets de **título** análogos (`TITLE_PRESETS`: intro, lower-third, créditos) con
  estilo + animación por defecto.

Los presets son datos; añadir uno nuevo no toca el render. Tests puros comprueban que cada
preset produce un `SubtitleStyle`/`TitleStyle` válido.

---

## 7. Tipografías personalizadas

Objetivo: que la MISMA fuente se vea igual en preview (navegador) y export (Chromium
headless de Remotion).

- **Google Fonts vía Remotion**: añadir dependencia `@remotion/google-fonts` a
  `remotion/package.json` y `frontend/package.json` (misma major `^4.0.0`). Un módulo
  compartido `fonts.ts` (en `remotion/src/` y espejo en `frontend/lib/editor/`) expone un
  catálogo curado (p. ej. Inter, Montserrat, Roboto, Bebas Neue, Anton, Oswald, Lobster)
  y una función `loadFont(family)` que llama al loader de `@remotion/google-fonts`. La
  composición invoca `loadFont(style.font_family)` para cada fuente usada, de modo que
  Remotion espera la carga antes de renderizar (determinista en headless).
- **Fuentes locales empaquetadas**: para fuentes propias/libres se colocan en
  `remotion/public/fonts/` y se cargan con `@remotion/fonts` (`loadFont` por `src`
  `staticFile(...)`). El frontend las sirve igual (ruta pública). Un `FONT_CATALOG`
  (datos) lista `{family, source: "google"|"local", file?}`.
- **Contrato**: `SubtitleStyle.font_family`/`TitleStyle.font_family` guardan el nombre de
  familia del catálogo. El inspector del frontend ofrece solo familias del catálogo (no
  texto libre) para garantizar que preview y export cargan la misma fuente.
- **Riesgo de licencia**: se documenta (abajo y en `NOTICE`) que solo se empaquetan
  fuentes con licencia libre (OFL/Apache). Las Google Fonts se cargan por su loader (no se
  redistribuyen ficheros) salvo las que se empaqueten explícitamente con su licencia.

---

## 8. Posicionamiento drag&drop (frontend)

- En `frontend/components/EditorWorkspace.tsx`, sobre el `@remotion/player`, se superpone
  una capa transparente de interacción (solo cuando hay un clip de título/overlay/subtítulo
  seleccionado). Al arrastrar, se calcula la posición normalizada (fracción 0..1 del ancho/
  alto del Player) y se traduce a:
  - título/overlay → `op set_position {anchor_x, anchor_y}`,
  - subtítulo → `op set_style {x, y}` (en px respecto a su posición de anclaje).
- El drag actualiza el estado React **de inmediato** (preview en tiempo real vía
  `compileEditInputProps`) y persiste con `PUT`/`op` al soltar (una sola `Version` por
  gesto, no por cada píxel). Las zonas seguras del preset activo se dibujan como guías.
- El inspector gana secciones por tipo de clip: **Subtítulo** (preset, karaoke, contorno/
  sombra/fondo, fuente), **Título** (texto, estilo, animación, tipo intro/lower-third/
  créditos), **Overlay** (asset/logo, opacidad, tamaño, rotación, mosaico). Reutiliza los
  primitivos de `frontend/components/ui.tsx`.
- Subida de overlay/logo: nuevo control que llama al endpoint de subida de overlay (§9) y,
  al terminar, ejecuta `op add_overlay` con el `asset_id` devuelto.

La verificación automática del drag&drop no es posible en el sandbox (requiere navegador);
se cubre con tests de la función pura de mapeo coordenada→`anchor`/`x,y` y un **smoke
manual documentado** (abrir editor, arrastrar, comprobar que el preview y el export mueven
el elemento al mismo sitio).

---

## 9. Subida de overlays / logos (API)

Nuevo endpoint generalizando `upload_scene_image`:

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/projects/{id}/overlays/upload` | Sube una imagen (PNG/JPG/WEBP/GIF/SVG) a nivel de **proyecto** (logo/marca de agua/emoji gráfico). Valida tipo/tamaño/Pillow (SVG: validación de tamaño + MIME), guarda en `overlays/uploads/` del workspace, crea `Asset(kind='image', provider='upload', scene_id=None)`. Devuelve `AssetRead`. |

El overlay se referencia luego por `op add_overlay`. Se reutiliza `GET /assets/{id}/
download` para servirlo al Player. Límite de tamaño y formatos documentados (riesgo:
assets grandes → se mantiene el `_MAX_UPLOAD_BYTES` existente, configurable).

---

## 10. Paridad y "no romper nada": invariantes de regresión

- Un `EditProject` sin campos nuevos produce **exactamente** el mismo `inputProps` que hoy
  (los campos nuevos tienen default neutro; `karaoke=false`, sin `title/overlay`). Test de
  regresión: `compile_remotion_props(viejo) == snapshot actual`.
- Los dos espejos de `EditComposition` se mantienen en paridad: un test de frontend
  (typecheck) + revisión de que ambos ficheros aplican la misma lógica (checklist en el
  PR). El compilador backend y el espejo `compose.ts` emiten la misma forma (test de forma
  en ambos lados sobre un `EditProject` fijo).
- Export sigue `muted=true` + `AudioMixer` −14 LUFS: audio idéntico, sin regresión de
  loudness.
- La composición `Timeline` de generación y el pipeline `generate_video` no se tocan.

---

## 11. Estrategia de pruebas (alineada con el sandbox)

- **Lógica pura (sin render):** `operations.py` (nuevas ops), `presets.py`,
  `subtitle_formats.py` (round-trip SRT/VTT/ASS: `parse(dump(cues)) == cues`), `convert.py`
  (rellena `words` desde un `palabras.json` fixture), `compile.py` (karaoke/títulos/
  overlays en `inputProps`). `pytest` determinista.
- **Export con ffprobe (skip con razón):** un test construye un `EditProject` con un clip
  de subtítulo karaoke + un título + un overlay (imagen placeholder real) + voz WAV real,
  ejecuta `compose_edit` y verifica con `ffprobe` que el MP4 tiene vídeo+audio y duración
  coherente. Si Remotion no puede renderizar (sin Chromium/glibc), **skip con razón**.
  (El render Remotion headless SÍ corre en este sandbox, así que se espera que pase.)
- **Frontend/remotion:** `npm run build`, `npm run lint`, `npm run typecheck` (frontend) y
  `npm run typecheck` (remotion). El drag&drop y el Player se validan con build/typecheck +
  **smoke manual documentado**.
- **Comandos de referencia:**
  - Backend: `cd backend && . .venv/bin/activate && AIV_FFMPEG_BIN=/projects/sandbox/.localbin/ffmpeg AIV_FFPROBE_BIN=/projects/sandbox/.localbin/ffprobe pytest -q`
  - Frontend: `cd frontend && npm run build && npm run lint && npm run typecheck`
  - Remotion: `cd remotion && npm run typecheck`

---

## 12. Riesgos y notas honestas

- **Fuentes y licencia.** Solo se empaquetan/usan fuentes con licencia libre (OFL/Apache).
  `@remotion/google-fonts` carga por su loader; empaquetar ficheros propios exige incluir
  su licencia en `NOTICE`. Riesgo si el usuario pide una fuente propietaria: no se incluye;
  el catálogo es cerrado por diseño.
- **Emojis / glifos.** El render de emojis depende de la fuente de emoji disponible en el
  Chromium de Remotion; puede diferir entre preview (navegador del usuario) y headless. Por
  eso los emojis "seguros" se tratan como **overlay gráfico** (PNG/SVG subido) cuando se
  requiere fidelidad exacta; los emojis de texto se documentan como "según plataforma".
- **ASS complejo.** El parser ASS del MVP conserva el texto plano de cada `Dialogue` e
  ignora overrides de estilo inline (`{\pos}`, `{\c}`...). Import/export preservan tiempos y
  texto; el estilo fino va por el `SubtitleStyle` del editor, no por el ASS. Se documenta.
- **Tamaño de assets.** Logos/overlays grandes aumentan el peso del workspace y el tiempo
  de render; se mantiene el límite de subida y se recomienda PNG optimizado.
- **Karaoke sin ASR.** Si un subtítulo no tiene `palabras.json` (creado a mano/importado),
  el resaltado usa reparto uniforme por palabra (aproximado), etiquetado como tal; nunca se
  presenta como transcripción real.
- **Paridad de los dos espejos.** Es el riesgo operativo principal: cada cambio de render
  debe replicarse en ambos `EditComposition`. Mitigación: PR checklist + test de forma del
  compilador en los dos lados.

---

## 13. Resumen del desglose de tareas (ver `.agents/tasks/task-subtitulos-titulos-pro/`)

1. **FEAT-001 (vertical slice karaoke, end-to-end):** `words` en modelo + `convert` desde
   `palabras.json` + `op set_karaoke` + karaoke en los DOS `EditComposition` + compilador +
   test de export con ffprobe. Demuestra la cadena completa preview↔export.
2. **FEAT-002 (import/export SRT/VTT/ASS):** `subtitle_formats.py` + endpoints import/export
   + tests round-trip.
3. **FEAT-003 (contornos/sombras/fondos + presets de subtítulo, incl. TikTok/Reels/Shorts +
   formato 9:16/zonas seguras):** `SubtitleStyle` ampliado + `presets.py` + ops + render.
4. **FEAT-004 (títulos animados y créditos):** tipo de pista/clip `title` + `TitleStyle`/
   `TitleAnimation` + ops + `TitleOverlay` en ambos espejos + compilador + tests.
5. **FEAT-005 (overlays gráficos/logos/marcas de agua + subida):** tipo `overlay` +
   `OverlayStyle` + endpoint de subida + ops + `OverlayClip` en ambos espejos + tests.
6. **FEAT-006 (tipografías personalizadas):** `@remotion/google-fonts`/`@remotion/fonts` +
   catálogo + `loadFont` en la composición + selector en inspector + typecheck/build.
7. **FEAT-007 (drag&drop + inspector pro):** capa de interacción sobre el Player, mapeo
   coordenada→`op set_position`/`set_style`, secciones de inspector, guías de zona segura +
   función pura de mapeo testeada + smoke manual.

> FEAT-001 es la primera por ser el slice vertical que prueba la paridad; el resto son en
> buena medida independientes entre sí (2, 3 y 6 pueden paralelizarse; 4 y 5 comparten el
> patrón de "nuevo tipo de clip/pista"; 7 depende de que existan títulos/overlays de 4/5).
