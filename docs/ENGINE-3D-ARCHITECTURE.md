# Motor 3D / Blender (Fase 3) — Arquitectura

Este documento especifica cómo se implementa el **motor 4 (3D/Blender)** del proyecto
`audiovisual-ia` como un proveedor de clips de vídeo conmutable que corre Blender en modo
headless, reutilizando intacto el pipeline existente (voz, subtítulos, timeline, mux a
-14 LUFS) y **sin romper** el MVP 2D ni la vía futura de proveedores de vídeo IA de pago
(Veo/Runway/Kling).

Es el documento de ARQUITECTURA. El alcance concreto del primer MVP (estilo 3D
estilizado) está en [`ENGINE-3D-MVP-STYLIZED.md`](ENGINE-3D-MVP-STYLIZED.md).

> Estado: **PLAN**. Nada de esto está implementado todavía. El stub
> `backend/app/engines/engine3d/__init__.py` existe con un docstring y lanza
> `NotImplementedError` hasta la Fase 3. Este plan define exactamente qué construir.

---

## 1. Principio rector: continuidad de identidad por modelo 3D riggeado

El argumento central del motor 3D frente a los generadores de vídeo IA (de pago):

- Ningún modelo de vídeo IA garantiza hoy que el **mismo personaje** conserve rostro,
  proporciones y vestuario entre tomas. Deriva la apariencia escena a escena.
- Con Blender, el personaje principal es **un único modelo 3D riggeado**. Es *literalmente
  el mismo objeto* en todas las escenas: mismo mesh, mismas texturas, mismo material,
  mismo esqueleto. La identidad visual es constante **por construcción**, no por suerte
  del modelo generativo.
- El coste es el tiempo de render. El usuario ha decidido que **el tiempo de render no es
  una restricción** ("no importa si es lento ni si renderiza paso por paso"). Eso es lo
  que hace viable el 3D local y gratuito frente al vídeo IA de pago.

De aquí se derivan dos decisiones:

1. **Render EEVEE primero** (rápido, cabe en la RTX 3050 de 4 GB de VRAM del usuario),
   **Cycles como opción LENTA opcional** (más calidad, mucho más coste).
2. El personaje, el set, las animaciones y las cámaras son **assets y parámetros**, no
   salidas de un modelo generativo. Se componen deterministamente en un script `.py` de
   Blender que el motor genera y ejecuta.

---

## 2. Dónde encaja: la interfaz "escena → clip de vídeo"

`docs/ARCHITECTURE.md` (§2 y §9) ya reserva el motor 4 con la interfaz borrador
`Engine3D.render_sequence(scene3d) -> VideoClip` y el patrón "escena → clip de vídeo",
donde un clip puede ser una imagen estática (Fase 1) o un clip animado (Fases 2/3). Este
plan concreta esa interfaz **sin cambiar la forma** del modelo de datos ni de la API.

### 2.1 Interfaz pública del motor

Paquete `backend/app/engines/engine3d/`. Interfaz concreta propuesta (reemplaza el
`NotImplementedError` del stub, pero las implementaciones reales de Blender siguen
lanzándolo cuando Blender no está disponible, nunca simulan un clip):

```python
# backend/app/engines/engine3d/engine.py  (a implementar en Fase 3)

@dataclass(frozen=True)
class Scene3DSpec:
    """Parámetros deterministas de UNA escena 3D (derivados de Scene + guion 3D)."""
    scene_id: int
    order: int
    duration_s: float
    character_id: str          # id del personaje riggeado (identidad constante)
    character_animation: str   # nombre de clip de animación (idle, walk, point, react...)
    set_id: str                # id del escenario/set 3D
    camera: "CameraSpec"       # plano, encuadre, movimiento (ver §5)
    style_preset: str          # "stylized" (MVP) | "realistic" (futuro)
    width: int                 # 1080 (vertical 9:16)
    height: int                # 1920
    fps: int                   # 30 por defecto (AIV_FPS)
    render_engine: str         # "eevee" | "cycles"
    seed: int | None = None

@dataclass(frozen=True)
class VideoClip:
    """Clip de vídeo de una escena producido por Blender (lo consume el timeline)."""
    path: str                  # ruta al .mp4/.mov del clip de la escena
    scene_id: int
    duration_s: float
    width: int
    height: int
    fps: int
    provider: str              # "blender-eevee" | "blender-cycles"
    meta: dict                 # script usado, nº frames, tiempos de render, warnings...

class Engine3D(Protocol):
    def render_sequence(self, spec: Scene3DSpec, out_dir: str) -> VideoClip: ...
    def available(self) -> bool: ...      # ¿hay binario de Blender utilizable?
```

### 2.2 Separación lógica pura vs. ejecución de Blender (clave para la testabilidad)

El motor se parte en dos capas con una frontera dura:

1. **Capa pura (sin Blender, 100% testeable en el sandbox):**
   - `script_builder.py`: a partir de `Scene3DSpec` genera el **texto del script `.py`**
     que Blender ejecutará (import del personaje/set, colocación de cámara, aplicación de
     la animación, rango de frames, ajustes de EEVEE/Cycles, salida de vídeo). Es una
     función pura `build_blender_script(spec, assets) -> str`.
   - `mapping.py`: mapea `Scene` (narración, `camera_notes`, `duration_s`) + el guion 3D a
     un `Scene3DSpec` (elección de animación, set, cámara). Función pura.
   - `output_parser.py`: interpreta stdout/stderr y el fichero de salida de Blender para
     construir el `VideoClip` (nº de frames, ruta, warnings). Función pura sobre strings.
   - `assets.py`: catálogo de personajes/sets/animaciones disponibles y resolución de sus
     rutas en disco (validación de existencia/licencia). Pura salvo el `os.path.exists`.

2. **Capa de ejecución (requiere Blender real, se SALTA en el sandbox):**
   - `runner.py`: localiza el binario (`AIV_BLENDER_BIN`), invoca
     `blender -b -P <script.py> -- <args>` como subproceso, controla timeout y código de
     salida, y entrega stdout/stderr al `output_parser`. Si no hay binario, `available()`
     devuelve `False` y `render_sequence` lanza `Engine3DUnavailable` (análogo a
     `RenderUnavailable` del motor 6). **Nunca produce un clip simulado.**

Esta frontera es la misma filosofía que ya usa el motor de render 2D: `RenderEngine`
lanza `RenderUnavailable` cuando Chromium/glibc no están disponibles y el pipeline lo
propaga en vez de inventar un MP4 (ver `backend/app/engines/render/engine.py`). El motor
3D replica ese patrón con `Engine3DUnavailable`.

---

## 3. Selección de motor 2D vs. 3D: conmutabilidad sin romper nada

Hay **dos ejes de conmutación** y el plan los mantiene independientes y aditivos:

### 3.1 Eje A — tipo de producción (`Project.production_type`)

`Project.production_type` ya existe en el modelo (`backend/app/models/entities.py`,
default `"2d"`). El orquestador del pipeline elige la **fuente visual por escena** según
este campo:

- `production_type == "2d"` → `VisualEngine.render_scene_image(...)` → `Asset(kind='image')`
  (comportamiento ACTUAL, intacto).
- `production_type == "3d"` → `Engine3D.render_sequence(...)` → `Asset(kind='video', scene_id=...)`.

El resto del pipeline (guion, voz, subtítulos, música/SFX, timeline, mux) **no cambia**.
La única bifurcación está en el paso "fuente visual por escena" de `pipeline.py`
(`_generate_scene_assets`). El default del sistema sigue siendo `"2d"`: un proyecto 2D
existente se comporta exactamente igual.

### 3.2 Eje B — proveedor de clip de vídeo (registry, futuro de pago PRESERVADO)

Para que la vía de **vídeo IA de pago** (Veo/Runway/Kling) siga siendo un futuro vivo, el
motor 3D se registra como **un proveedor más de una nueva capacidad conmutable**: `"clip"`
(clip de vídeo por escena). Se añade al `registry` existente
(`backend/app/providers/registry.py`) siguiendo el patrón `AIV_*_BACKEND`:

```
CAPABILITIES = ("llm", "image", "tts", "asr", "clip")   # se AÑADE "clip"

PROVIDERS["clip"] = {
    "blender": BlenderClipProvider,   # motor 3D local/gratuito (este plan)
    # Futuro de pago (stubs, NO implementados; preservan la vía):
    # "veo": VeoClipProvider, "runway": RunwayClipProvider, "kling": KlingClipProvider,
    "fake": FakeClip,                 # solo tests
}
```

- Default `AIV_CLIP_BACKEND=blender`. Cambiar a un proveedor de pago en el futuro es
  implementar la interfaz `ClipProvider` y registrarla, sin tocar motores, API ni
  frontend (invariante central de §3 de la arquitectura).
- Los **stubs de pago** (Veo/Runway/Kling) se dejan declarados como comentarios/factorías
  que lanzan `NotImplementedError`, igual que hoy `ElevenLabsTTS`/`OpenAITTS` conviven con
  los defaults locales. **No se elimina ni se simplifica** esa posibilidad: el motor 3D se
  SUMA como el default local/gratuito de la capacidad `"clip"`.

> Relación entre los dos ejes: `production_type` decide *si* se usa clip de vídeo por
> escena; la capacidad `"clip"` del registry decide *qué proveedor* produce ese clip
> (Blender local hoy; Veo/Runway/Kling de pago mañana). Un proyecto `3d` usa
> `AIV_CLIP_BACKEND=blender`; un futuro proyecto `ia_video` podría usar `veo` sin tocar el
> resto del sistema.

---

## 4. Cómo el timeline / Remotion / FFmpeg consumen un CLIP de vídeo por escena

Hoy `TimelineBuilder` (`backend/app/engines/render/timeline.py`) toma el `Asset(kind='image')`
más reciente por `scene_id` y Remotion lo pinta a pantalla completa con `<Img>`. Para el
3D, cada escena produce un `Asset(kind='video')` (un clip animado). **Enfoque elegido y
justificación:**

### 4.1 Enfoque elegido: clip por escena en Remotion vía `<OffthreadVideo>`

Se extiende la cadena existente (timeline JSON → Remotion → FFmpeg) en vez de crear una
segunda ruta de composición. Razones:

- Reutiliza TAL CUAL el mux de audio por escena y la mezcla a -14 LUFS: el `RenderEngine`
  ya secuencia la voz por el offset de cada escena (`_audio_tracks`) y muxea con FFmpeg
  sobre el vídeo que produce Remotion. Si Remotion emite el vídeo con los clips 3D en vez
  de imágenes, **el audio y los subtítulos siguen funcionando sin cambios**.
- Reutiliza TAL CUAL la superposición de subtítulos (los `cues` por escena se dibujan
  encima, igual que sobre la imagen). Los subtítulos TikTok del estilo estilizado son una
  mejora del estilado de ese overlay, no una ruta nueva.
- Mantiene **una sola** capa de composición final (requisito de §9 de la arquitectura:
  "Remotion/FFmpeg siguen siendo la capa de composición final").

Cambios concretos:

1. **Timeline (backend):** añadir a `TimelineScene` un campo opcional `video_src` (alias
   `videoSrc`) análogo a `image_src`. `TimelineBuilder._latest_by_scene(..., kind="video")`
   indexa el clip por escena; si existe, se emite `videoSrc`; si no, se mantiene `imageSrc`
   (retrocompatible con 2D). La regla de duración por escena **ya** prioriza la voz, luego
   `Scene.duration_s`; para 3D la duración del clip debe cuadrar con la de la escena (el
   `script_builder` fija el rango de frames a `round(duration_s * fps)`).
2. **Remotion (`TimelineComposition.tsx` + `types.ts`):** si `videoSrc` está presente,
   renderizar `<OffthreadVideo src={resolveSrc(videoSrc)} />` a pantalla completa en lugar
   de `<Img>`; el resto (overlay de subtítulos, `<Sequence>` por escena) es idéntico.
   `<OffthreadVideo>` es la API de Remotion recomendada para componer vídeo pregrabado
   cuadro a cuadro durante el render.
3. **Rutas estáticas:** el clip 3D vive bajo `AIV_MEDIA_ROOT` (workspace del proyecto), así
   que se emite **relativo al publicDir** igual que las imágenes (`_to_static_src`), y
   `staticFile` lo resuelve. Sin cambios en `render.mjs` salvo que ya pasa `--public-dir`.

### 4.2 Enfoque alternativo considerado (y descartado para el MVP)

**Concatenar los clips 3D directamente con FFmpeg** (concat demuxer) y añadir subtítulos
quemados con el filtro `subtitles`/`drawtext`, saltándose Remotion. Es más simple en
cómputo y evita Chromium, **pero** duplicaría la lógica de subtítulos y de secuenciado de
audio que ya vive en Remotion + `RenderEngine`, y rompería "una sola capa de composición".
Se documenta como **optimización futura opcional** (p. ej. un `AIV_CLIP_COMPOSITOR=ffmpeg`)
para entornos sin Chromium, pero **no** es el camino del MVP.

---

## 5. Cámara, animación y estilo (parámetros deterministas)

El estilo estilizado exige dirección de cámara variada y cambio de plano cada 2-4 s. Se
modela como parámetros puros que el `script_builder` traduce a Blender:

```python
@dataclass(frozen=True)
class CameraSpec:
    shot: str        # "closeup" | "medium" | "wide" | "aerial" | "low_angle" | "over_shoulder"
    move: str        # "static" | "zoom_in" | "zoom_out" | "dolly" | "orbit" | "pan"
    focal_mm: float  # p. ej. 35/50/85 (equivalente estilizado)
    dof: bool        # profundidad de campo (desenfoque de fondo cinematográfico)
```

- El mapeo `Scene.camera_notes` (texto del guion, ya existente) → `CameraSpec` es una
  función pura con un diccionario de palabras clave (es/en) y valores por defecto seguros.
- La animación corporal (idle/walk/turn_head/point/react) se elige por escena desde un
  catálogo acotado (ver MVP). El set y la iluminación cinematográfica (key/fill/rim +
  volumétrica en EEVEE) son presets del `style_preset`.
- `style_preset="stylized"` (MVP) y `style_preset="realistic"` (futuro) seleccionan
  distintos materiales/shaders, iluminación y, posiblemente, motor (EEVEE vs Cycles). La
  interfaz es la misma; solo cambian los presets y los assets.

---

## 6. Configuración (variables `AIV_*`)

Se añaden a `backend/app/config.py` (Pydantic `Settings`, `env_prefix="AIV_"`) y a
`.env.example`, siguiendo el patrón existente (sin hardcodear binarios; rutas
configurables porque en el sandbox los binarios viven fuera del PATH):

| Variable | Default | Descripción |
|---|---|---|
| `AIV_CLIP_BACKEND` | `blender` | Proveedor de la capacidad `clip` (`blender`; futuro `veo`/`runway`/`kling`). |
| `AIV_BLENDER_BIN` | `blender` | Ruta/nombre del binario de Blender 4.x (como `AIV_FFMPEG_BIN`). |
| `AIV_RENDER_ENGINE_3D` | `eevee` | Motor de render de Blender: `eevee` (rápido) \| `cycles` (lento, opcional). |
| `AIV_BLENDER_ASSETS_DIR` | `` (vacío) | Carpeta de assets 3D (personajes/sets/animaciones). Vacío = `media/assets3d`. |
| `AIV_BLENDER_SAMPLES` | `32` | Muestras de render (EEVEE/Cycles); sube calidad a costa de tiempo. |
| `AIV_BLENDER_TIMEOUT_S` | `3600` | Timeout del subproceso de Blender por escena (el tiempo no es restricción). |
| `AIV_PRODUCTION_TYPE` | `2d` | Default de `production_type` para proyectos nuevos (`2d` \| `3d`). Override por proyecto en BD. |

Sin ninguna de estas variables el sistema sigue siendo el MVP 2D actual. `production_type`
se resuelve por proyecto (campo en BD) con `AIV_PRODUCTION_TYPE` como default global,
igual que los backends se resuelven por `provider_overrides` con el env var como default.

---

## 7. EEVEE vs Cycles (hardware del usuario: RTX 3050 4 GB)

- **EEVEE (default, `AIV_RENDER_ENGINE_3D=eevee`):** rasterizador en tiempo casi real.
  Cabe cómodamente en 4 GB de VRAM, soporta iluminación volumétrica, sombras, DOF y
  bloom suficientes para el estilo caricaturesco/estilizado. Es el camino del MVP.
- **Cycles (opcional, `AIV_RENDER_ENGINE_3D=cycles`):** path tracer físicamente correcto;
  mejor para el estilo fotorrealista futuro. Mucho más lento y más sensible a la VRAM
  (4 GB obliga a tiles pequeños / fallback a CPU). Se documenta como opción LENTA; **no**
  es el objetivo del MVP estilizado.
- Como el tiempo de render no es restricción, el motor puede renderizar **frame a frame**
  y el `runner` reporta progreso por frame (parseando stdout de Blender) para el panel de
  jobs. Un render largo no bloquea: es un job de la cola existente.

---

## 8. Reutilización del pipeline existente (lo que NO cambia)

Resumen explícito de qué se reutiliza intacto, para dejar claro que el 3D solo cambia la
**fuente visual por escena**:

- **Guion (motor 1):** idéntico. El guion 3D añade, como mucho, pistas de animación/cámara
  en `camera_notes` (campo ya existente); el `ScriptEngine` no cambia de forma.
- **Voz (motor 5):** idéntico. `_synthesize_scene_voice` por escena y la secuenciación por
  offset (FIX-A) se reutilizan sin cambios.
- **Subtítulos:** idéntico. `generate_subtitles` + los `cues` por escena se reutilizan; el
  estilado TikTok es una mejora del overlay en Remotion, no una ruta nueva.
- **Música/SFX y mezcla a -14 LUFS:** idéntico (`AudioMixer`, `_audio_tracks`).
- **Timeline + mux (motor 6):** se EXTIENDE con `video_src`/`<OffthreadVideo>`; el mux
  FFmpeg final no cambia.
- **Cola de jobs, cache de assets, API, modelo de datos:** sin cambios de forma. El clip
  3D es un `Asset(kind='video', scene_id=...)` (el `kind='video'` ya existe en el enum de
  `Asset`).

---

## 9. Riesgo de entorno: Blender en el sandbox

- **Hallazgo (verificado):** en el sandbox de desarrollo `which blender` y
  `blender --version` NO devuelven nada: **Blender no está instalado**. El `$HOME` y el
  gestor de paquetes del sistema son de **solo lectura** y **no hay GPU**, por lo que es
  muy improbable poder instalar/ejecutar Blender aquí.
- **Consecuencia de diseño:** el render 3D real **solo se verifica en el PC del usuario**
  (Blender 4.x + RTX 3050 + EEVEE). En el sandbox:
  - La **capa pura** (generación del script `.py`, mapeo escena→cámara→animación,
    mapeo guion→escena 3D, parsing de salida, extensión del timeline con `video_src`, la
    composición Remotion con `<OffthreadVideo>` en modo type-check/lint) **SÍ se testea
    unitariamente** con pytest, sin Blender.
  - La **capa de ejecución** (invocar Blender y renderizar un clip real) se **SALTA** con
    `pytest.mark.skipif(not Engine3D().available(), reason="Blender no disponible")` y una
    razón explícita. **Nunca** se simula un clip ni se presenta un placeholder como render
    3D real (requisito §9 del prompt maestro).
- Instalación en el PC del usuario (documentada en el MVP): Blender 4.x desde
  [blender.org](https://www.blender.org/download/) o gestor de paquetes; apuntar
  `AIV_BLENDER_BIN` al ejecutable; verificar `blender --version` ≥ 4.0.

---

## 10. Qué se preserva de la vía de pago (no se elimina nada)

- La capacidad `"clip"` del registry deja **registrados como stubs** los proveedores de
  pago Veo/Runway/Kling (factorías que lanzan `NotImplementedError`), exactamente como hoy
  conviven `ElevenLabsTTS`/`OpenAITTS` con los defaults locales de TTS.
- `production_type` admite valores futuros adicionales sin cambiar el esquema.
- La arquitectura de 6 motores, la capa de proveedores y el modelo de datos **no se tocan
  en su forma**; el motor 3D encaja por los puntos de extensión ya previstos en
  `docs/ARCHITECTURE.md`. Nada del 2D ni de la vía de pago futura se elimina ni se
  simplifica.
