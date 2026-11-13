# MVP-3D — Estilo 3D ESTILIZADO (alcance concreto)

Este documento fija el alcance del **primer MVP del motor 3D** en estilo **3D estilizado**
(el "Prompt 2" del usuario). El estilo **fotorrealista** (su "Prompt 1") queda para
DESPUÉS y se contempla como un preset futuro (`style_preset="realistic"`). La arquitectura
general está en [`ENGINE-3D-ARCHITECTURE.md`](ENGINE-3D-ARCHITECTURE.md).

> Estado: **PLAN**. El desglose de tareas verificables vive en
> `.agents/tasks/task-fase3-mvp-3d-estilizado/`.

---

## 1. Objetivo de calidad (resumen honesto del "Prompt 2")

Vídeo vertical **9:16, 1080×1920, ~90 s**, 3D cinematográfico con:

- **Un personaje principal caricaturesco** con identidad visual CONSTANTE entre escenas:
  cabeza grande redondeada, ojos grandes expresivos, boca animada, cuerpo pequeño,
  extremidades definidas, expresiones exageradas, movimientos fluidos.
- **Escenarios 3D** con texturas, iluminación volumétrica, profundidad de campo y sombras.
- **Animación corporal**: caminar, girar la cabeza, señalar, reaccionar.
- **Dirección de cámara variada**: primer plano, zoom, travelling, frontal, plano general,
  aérea, contrapicado; **cambio de plano cada 2-4 s**.
- **Iluminación cinematográfica** (key/fill/rim + volumétrica).
- **Subtítulos grandes en español**: blanco + contorno negro + **palabra clave en
  amarillo**, en la zona segura vertical (estilo TikTok).
- **Reglas duras:** nada de 2D, ni imágenes estáticas como sustituto, ni personajes
  rígidos, ni cambiar la apariencia del personaje, ni deformaciones, ni marcas de agua, ni
  interfaces de redes sociales, ni textos aleatorios.

### 1.1 Qué es alcanzable en el MVP vs. qué requiere iteración (honestidad)

> Versión en **tabla** (por capacidad, con estado MVP / iteración / fase futura) en
> [`ENGINE-3D-RUNBOOK.md`](ENGINE-3D-RUNBOOK.md) §7.


**Alcanzable en el MVP (con assets libres Mixamo/CC0 + EEVEE):**
- Identidad de personaje constante (es el mismo modelo 3D riggeado: garantizado por
  construcción).
- Animación corporal a partir de clips Mixamo (idle, walk, turn, point, react).
- Sets 3D sencillos CC0 con iluminación cinematográfica EEVEE (key/fill/rim +
  volumétrica), DOF y sombras.
- Dirección de cámara por escena (plano + movimiento) y corte cada 2-4 s por el troceado
  de escenas del guion.
- Subtítulos TikTok (blanco + contorno negro + palabra clave amarilla) en zona segura 9:16.
- Vertical 9:16 1080×1920, ~90 s, ensamblado con la voz y los subtítulos existentes.

**Requiere iteración (NO prometido en el MVP; se documenta como trabajo futuro):**
- **Lip-sync fino** boca↔fonemas. El MVP hace, como mucho, una boca animada
  genérica/parpadeo y gestos; el lip-sync basado en visemas/ASR es iteración posterior.
- **Expresiones faciales exageradas tipo Pixar** con blendshapes ricos: depende de que el
  modelo libre traiga shape keys; si no, el MVP se limita a pose corporal + cabeza.
- **"Calidad Pixar"**: se tiende iterando (mejores modelos, materiales, luces, Cycles). El
  MVP busca un estilizado *coherente y agradable*, no paridad con un estudio.
- **Fotorrealismo** (Prompt 1): preset futuro `realistic`, probablemente Cycles; fuera de
  este MVP.

---

## 2. Vertical slice del MVP (lo que entrega la primera feature funcional)

Un camino **end-to-end mínimo pero real**, verificable en el PC del usuario:

```
proyecto production_type=3d
  → guion (Ollama o fixture) → escenas con camera_notes
  → POR ESCENA: Engine3D.render_sequence(Scene3DSpec) → Blender headless (EEVEE)
       · importa el MISMO personaje riggeado (identidad constante)
       · lo coloca en un set simple con iluminación cinematográfica
       · aplica 1-2 animaciones (p. ej. idle + walk/point)
       · coloca la cámara según camera_notes (plano + movimiento)
       · renderiza un clip .mp4 de duration_s a 1080×1920
    → Asset(kind='video', scene_id=...)
  → voz por escena (FIX-A, REUTILIZADA) + subtítulos (REUTILIZADOS, estilo TikTok)
  → timeline JSON con videoSrc por escena
  → Remotion <OffthreadVideo> por escena + overlay subtítulos
  → FFmpeg mux a -14 LUFS → MP4 vertical final
```

En el sandbox se verifica TODO menos el render real de Blender (que se salta con razón);
en el PC del usuario se verifica el MP4 completo.

---

## 3. Catálogo de assets del MVP (acotado)

Para mantener el MVP cerrado y verificable, el catálogo inicial es pequeño y fijo:

- **Personaje:** 1 personaje principal estilizado riggeado (rig humanoide compatible
  Mixamo). Un único `.blend`/`.glb` que se importa igual en todas las escenas → identidad
  constante.
- **Animaciones:** conjunto acotado reutilizable (nombres estables usados por `mapping.py`):
  `idle`, `walk`, `turn_head`, `point`, `react`.
- **Sets:** 1-2 escenarios CC0 sencillos (p. ej. `room_interior`, `outdoor_street`) con
  presets de iluminación cinematográfica.
- **Cámara:** planos `closeup|medium|wide|aerial|low_angle|over_shoulder` × movimientos
  `static|zoom_in|zoom_out|dolly|orbit|pan`.

Ampliar el catálogo (más personajes, sets, animaciones) es añadir assets + entradas en
`assets.py`, sin tocar el motor.

---

## 4. Mapeo guion → escena 3D

Función pura `mapping.py` (testeable sin Blender):

- `Scene.duration_s` (o la voz) → `duration_s` del clip → rango de frames
  `round(duration_s * fps)`.
- `Scene.camera_notes` → `CameraSpec` (diccionario de palabras clave es/en → plano +
  movimiento; defaults seguros si no hay pista).
- Heurística de animación por escena a partir de la narración/`camera_notes`
  (p. ej. "camina"/"walk" → `walk`; "señala"/"point" → `point`; por defecto `idle`),
  con `turn_head`/`react` como gestos de transición.
- `set_id` por defecto (`room_interior`) salvo que el guion sugiera exterior.
- `style_preset="stylized"`, `render_engine=AIV_RENDER_ENGINE_3D` (EEVEE por defecto),
  `width=1080`, `height=1920`, `fps=AIV_FPS`.

El objetivo de **corte cada 2-4 s** se consigue porque el guion trocea en escenas de esa
duración; el motor 3D no necesita lógica de corte adicional en el MVP.

---

## 5. Subtítulos TikTok (estilo estilizado)

Se reutiliza el pipeline de subtítulos existente (`generate_subtitles` + `cues` por
escena); el estilo TikTok es una **mejora del overlay de Remotion** para
`production_type=3d` (o un preset de estilo de subtítulo):

- Fuente grande en negrita, **blanco con contorno negro** grueso (varias sombras/stroke),
  centrado en la **zona segura** vertical (no pegado a bordes).
- **Palabra clave en amarillo**: resaltar una palabra por cue. En el MVP, la "palabra
  clave" se elige de forma determinista (p. ej. la palabra más larga del cue, o marcada en
  el guion); el resaltado semántico fino es iteración futura.
- Sin marcas de agua ni interfaces de redes (regla dura del prompt).

---

## 6. Riesgos y licencias de assets (nota honesta)

- **Blender posiblemente no instalable en el sandbox** (verificado: no está; `$HOME` y
  paquetes del sistema de solo lectura; sin GPU). El render 3D real se valida en el PC del
  usuario; en el sandbox se testea solo la lógica pura y se salta el render con razón.
- **VRAM 4 GB (RTX 3050) → EEVEE** en el MVP. Cycles es opción lenta/futura.
- **Licencias de assets** (CRÍTICO; el repo ya mantiene `NOTICE` y
  `docs/REFERENCE-REPOS-LICENSES.md`):
  - **Mixamo** (Adobe): personajes/animaciones de uso libre con cuenta Adobe; revisar los
    términos de Mixamo para redistribución. Para evitar dudas de redistribución, los assets
    descargados por el usuario se guardan en `AIV_BLENDER_ASSETS_DIR` (fuera del control de
    versiones, como el resto de media), NO se commitean al repo.
  - **CC0** (p. ej. Poly Haven, Kenney, modelos CC0): dominio público, sin atribución
    obligatoria; preferidos para sets/props. Aun así se registra su origen en `NOTICE`.
  - Regla: **no se versiona ningún asset 3D de terceros** en el repo; el catálogo
    (`assets.py`) referencia rutas en `AIV_BLENDER_ASSETS_DIR` que el usuario puebla, y
    documenta la licencia de cada entrada. Se añade un `assets3d/README.md` explicando de
    dónde bajar cada asset libre y su licencia.
  - **No marcas de agua** ni logos de redes (regla del prompt): los assets libres elegidos
    no deben traerlas.
- **"Calidad Pixar" no está garantizada en el MVP**: se tiende iterando. El MVP garantiza
  identidad de personaje constante y un estilizado coherente, no paridad de estudio.

---

## 7. Qué queda explícitamente fuera del MVP (para el estilo realista / fases siguientes)

- Preset `realistic` (Prompt 1), probablemente con Cycles y materiales PBR.
- Lip-sync por visemas/ASR.
- Blendshapes faciales ricos / expresiones exageradas tipo estudio.
- Biblioteca grande de personajes/sets/animaciones y selección automática avanzada.
- Proveedores de vídeo IA de pago (Veo/Runway/Kling): permanecen como stubs conmutables
  en la capacidad `"clip"` del registry (ver arquitectura §3.2 y §10). El MVP-3D NO los
  implementa, pero NO los elimina.
