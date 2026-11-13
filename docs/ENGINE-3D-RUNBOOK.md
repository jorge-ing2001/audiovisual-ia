# Runbook del motor 3D: producir el MP4 vertical del MVP-3D en tu PC

Guia operativa, paso a paso y con comandos reales, para producir el video vertical
**9:16, 1080x1920, ~90 s** del MVP-3D en el **PC del usuario** (RTX 3050 4 GB, EEVEE),
reutilizando intacto el pipeline 2D (guion, voz, subtitulos, timeline, mux a -14 LUFS).

- Arquitectura del motor: [`ENGINE-3D-ARCHITECTURE.md`](ENGINE-3D-ARCHITECTURE.md).
- Alcance y estilo del MVP: [`ENGINE-3D-MVP-STYLIZED.md`](ENGINE-3D-MVP-STYLIZED.md).
- Catalogo y licencias de los assets 3D: [`../media/assets3d/README.md`](../media/assets3d/README.md).
- Arquitectura general del monorepo: [`ARCHITECTURE.md`](ARCHITECTURE.md).

> **Importante (honestidad del entorno).** El render 3D real de Blender **solo** se
> verifica en tu PC. En el sandbox de desarrollo Blender no esta instalado ni es
> instalable (sin GPU, `$HOME` y gestor de paquetes de solo lectura): alli se testea la
> logica pura y el render se SALTA con razon explicita, nunca se simula un clip. Este
> runbook describe la ejecucion real en tu maquina.

---

## 1. Requisitos previos

- El MVP 2D ya operativo (backend + Remotion + FFmpeg), segun el
  [`README.md`](../README.md) del repo.
- **Node 20+**, **Python 3.11**, y un **FFmpeg 7+** accesible
  (`AIV_FFMPEG_BIN` / `AIV_FFPROBE_BIN`).
- **GPU** NVIDIA RTX 3050 (4 GB) para EEVEE (recomendado). Sin GPU, EEVEE puede caer a CPU
  (mas lento); el tiempo de render no es una restriccion del MVP.
- Una **cuenta Adobe** (gratuita) para descargar el personaje y las animaciones de Mixamo.

---

## 2. Instalar Blender 4.x y apuntar `AIV_BLENDER_BIN`

1. Descarga Blender **4.x** desde [blender.org](https://www.blender.org/download/) o
   mediante el gestor de paquetes de tu sistema:

   ```bash
   # Linux (ejemplos; usa el que corresponda a tu distro)
   sudo apt install blender          # Debian/Ubuntu (verifica que sea 4.x)
   sudo dnf install blender          # Fedora
   sudo snap install blender --classic
   # macOS
   brew install --cask blender
   # Windows: instalador .msi de blender.org, o `winget install BlenderFoundation.Blender`
   ```

2. Verifica que la version es **>= 4.0**:

   ```bash
   blender --version
   # Blender 4.x.y   <-- debe empezar por 4.
   ```

3. Apunta `AIV_BLENDER_BIN` al ejecutable en tu `.env` (copiado de `.env.example`). Si
   `blender` esta en el `PATH` basta el nombre; si no, usa la ruta absoluta:

   ```bash
   # .env
   AIV_BLENDER_BIN=blender
   # o, por ejemplo:
   # AIV_BLENDER_BIN=/usr/bin/blender
   # AIV_BLENDER_BIN=C:\Program Files\Blender Foundation\Blender 4.2\blender.exe
   AIV_RENDER_ENGINE_3D=eevee        # rapido, cabe en 4 GB VRAM (MVP). cycles = lento/opcional
   AIV_BLENDER_SAMPLES=32            # sube calidad a costa de tiempo
   AIV_BLENDER_TIMEOUT_S=3600        # timeout del subproceso de Blender por escena
   ```

> Si `AIV_BLENDER_BIN` no apunta a un Blender utilizable, el motor lanza
> `Engine3DUnavailable` con un mensaje accionable y el pipeline **propaga** el error (no
> inventa un clip). Corrige la ruta y vuelve a lanzar.

---

## 3. Poblar `AIV_BLENDER_ASSETS_DIR` (personaje, animaciones, sets)

Los assets 3D de terceros **no se versionan** en el repo: los descargas tu y los colocas en
`AIV_BLENDER_ASSETS_DIR` (si lo dejas vacio, se usa `media/assets3d`). El catalogo acotado
del MVP y su resolucion de rutas vive en
[`backend/app/engines/engine3d/assets.py`](../backend/app/engines/engine3d/assets.py).

### 3.1 Estructura de carpetas EXACTA que espera `assets.py`

```
$AIV_BLENDER_ASSETS_DIR/           (o media/assets3d/ si la variable esta vacia)
  characters/
    hero.blend                     # unico personaje riggeado (identidad constante)
  animations/
    idle.fbx
    walk.fbx
    turn_head.fbx
    point.fbx
    react.fbx
  sets/
    room_interior.blend
    outdoor_street.blend
```

Nombres e ids **estables** (los consume `assets.py` y `mapping.py`):

| Tipo | Subcarpeta | Ids / nombres de fichero | Extension |
|------|------------|--------------------------|-----------|
| Personaje | `characters/` | `hero` | `.blend` |
| Animaciones | `animations/` | `idle`, `walk`, `turn_head`, `point`, `react` | `.fbx` |
| Sets | `sets/` | `room_interior`, `outdoor_street` | `.blend` |

### 3.2 De donde bajar cada asset

- **Personaje riggeado + animaciones:** [Mixamo](https://www.mixamo.com/) (cuenta Adobe
  gratuita). Descarga un personaje humanoide estilizado como `hero.blend` (o importalo y
  guardalo como `.blend`) y las 5 animaciones como `.fbx` con los nombres de arriba.
- **Sets/props:** modelos **CC0** (p. ej. [Poly Haven](https://polyhaven.com/),
  [Kenney](https://kenney.nl/)) exportados a `room_interior.blend` y `outdoor_street.blend`.

El detalle de origen y licencia por asset esta en
[`../media/assets3d/README.md`](../media/assets3d/README.md).

### 3.3 Variable de entorno

```bash
# .env  (vacio = media/assets3d del repo)
AIV_BLENDER_ASSETS_DIR=/ruta/absoluta/a/tus/assets3d
```

> Si falta algun asset, el motor no simula: reporta exactamente que fichero falta y como
> obtenerlo (ver `MissingAsset.message()` en `assets.py`). Completa el catalogo antes de
> renderizar.

---

## 4. Crear un proyecto con `production_type='3d'` y aspecto 9:16

Arranca el backend (ver [`README.md`](../README.md)) y crea el proyecto. El eje de
produccion es `Project.production_type`: `2d` (intacto) | `3d` (clip por escena).

```bash
# Backend en http://localhost:8000 (uvicorn app.main:app)
curl -s -X POST http://localhost:8000/projects \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "demo-3d",
        "prompt": "Un heroe caricaturesco explica en 90 segundos como empezar en 3D",
        "production_type": "3d",
        "aspect_ratio": "9:16",
        "resolution": "1080p"
      }'
# -> {"id": 1, "production_type": "3d", "aspect_ratio": "9:16", ...}
```

Alternativa: el default global de `production_type` para proyectos nuevos se fija con
`AIV_PRODUCTION_TYPE=3d` en `.env`; cada proyecto puede sobreescribirlo en BD.

> El proveedor de la capacidad `clip` es **Blender** por defecto (`AIV_CLIP_BACKEND=blender`).
> Los stubs de pago (Veo/Runway/Kling) quedan registrados pero no implementados.

---

## 5. Lanzar el pipeline hasta el MP4

El pipeline end-to-end (guion -> clip 3D/voz por escena -> subtitulos -> timeline ->
Remotion `<OffthreadVideo>` -> mux a -14 LUFS) se dispara con un solo endpoint:

```bash
# Con prompt real (usa Ollama para el guion):
curl -s -X POST http://localhost:8000/projects/1/generate \
  -H 'Content-Type: application/json' -d '{}'
# -> 202 Accepted con el job encolado (polling de progreso en GET /jobs/{id})
```

Para verificar el camino 3D **sin depender de Ollama**, inyecta un guion fixture etiquetado
(queda marcado `source=fixture`, nunca se presenta como salida real de IA):

```bash
curl -s -X POST http://localhost:8000/projects/1/generate \
  -H 'Content-Type: application/json' \
  -d '{
        "script_fixture": {
          "language": "es",
          "scenes": [
            {"order": 1, "narration_text": "Hola, soy tu guia 3D.", "camera_notes": "primer plano, estatico", "duration_s": 4},
            {"order": 2, "narration_text": "Camino por la calle y te muestro el set.", "camera_notes": "plano general, travelling, exterior", "duration_s": 5},
            {"order": 3, "narration_text": "Te senalo lo importante y reacciono.", "camera_notes": "medio, zoom in", "duration_s": 4}
          ]
        }
      }'
```

Sigue el progreso por paso:

```bash
curl -s http://localhost:8000/jobs/<job_id>        # estado, progreso, detalle por paso
```

> Si Blender no esta disponible, el job falla con `Engine3DUnavailable` (mensaje accionable
> que cita `AIV_BLENDER_BIN`): corrige la instalacion/ruta y relanza. **Nunca** se produce
> un MP4 simulado.

---

## 6. Donde ver los clips 3D por escena y el MP4 final

Todo se escribe bajo `AIV_MEDIA_ROOT` (por defecto `./media`), en el workspace aislado del
proyecto `project_<id>/` (ver
[`backend/app/storage/media.py`](../backend/app/storage/media.py)):

```
$AIV_MEDIA_ROOT/project_<id>/
  clips3d/
    scene_<scene_id>/...mp4        # UN clip de video 3D por escena (EEVEE 1080x1920)
  voice/scene_<scene_id>/...       # voz por escena (reutilizada del MVP)
  srt/ , timeline/                 # subtitulos y timeline JSON (videoSrc por escena)
  video/                           # MP4 vertical FINAL muxeado a -14 LUFS
```

Descarga el MP4 final por API:

```bash
curl -s -L http://localhost:8000/projects/1/video -o demo-3d.mp4
```

Verificaciones rapidas del resultado (usa tu FFmpeg):

```bash
"$AIV_FFPROBE_BIN" -v error -select_streams v:0 \
  -show_entries stream=width,height -of csv=p=0 demo-3d.mp4
# -> 1080,1920   (vertical 9:16)
```

---

## 7. Alcanzable en el MVP vs. iteracion futura

Resumen honesto de expectativas (coherente con
[`ENGINE-3D-MVP-STYLIZED.md`](ENGINE-3D-MVP-STYLIZED.md) §1.1):

| Capacidad | Estado | Nota |
|-----------|--------|------|
| Identidad de personaje **constante** entre escenas | **Alcanzable (MVP)** | Garantizada por construccion: es el mismo `.blend` riggeado en todas las escenas. |
| **Animacion corporal** (idle/walk/turn_head/point/react) | **Alcanzable (MVP)** | Clips Mixamo aplicados por escena via `mapping.py`. |
| Sets CC0 + iluminacion cinematografica EEVEE (key/fill/rim, DOF, sombras) | **Alcanzable (MVP)** | Presets del `style_preset="stylized"`. |
| Direccion de camara por escena + corte cada 2-4 s | **Alcanzable (MVP)** | El troceado del guion da el corte; `CameraSpec` da plano+movimiento. |
| Subtitulos TikTok (blanco + contorno negro + palabra clave amarilla) en zona segura | **Alcanzable (MVP)** | Overlay de Remotion reutilizado/estilizado. |
| Vertical 9:16 1080x1920 ~90 s con voz y subtitulos | **Alcanzable (MVP)** | Mux a -14 LUFS reutilizado del MVP 2D. |
| **Lip-sync fino** boca<->fonemas | Iteracion | El MVP hace, como mucho, boca animada generica/parpadeo + gestos. |
| **Blendshapes faciales ricos** / expresiones exageradas tipo estudio | Iteracion | Depende de que el modelo libre traiga shape keys. |
| **"Calidad Pixar"** | Iteracion | Se tiende iterando (mejores modelos, materiales, luces). El MVP busca un estilizado coherente, no paridad de estudio. |
| **Fotorrealismo** (Prompt 1) / **Cycles** | Fase futura | Preset futuro `realistic`, probablemente Cycles y PBR; fuera del MVP. |

---

## 8. Variables `AIV_*` usadas en este runbook

Todas estan documentadas en [`../.env.example`](../.env.example) y en
[`backend/app/config.py`](../backend/app/config.py) (`env_prefix="AIV_"`):

| Variable | Default | Para que sirve aqui |
|----------|---------|---------------------|
| `AIV_CLIP_BACKEND` | `blender` | Proveedor de la capacidad `clip` (`blender`; futuro `veo`/`runway`/`kling`). |
| `AIV_BLENDER_BIN` | `blender` | Ruta/nombre del binario de Blender 4.x. |
| `AIV_RENDER_ENGINE_3D` | `eevee` | Motor de render de Blender (`eevee` rapido \| `cycles` lento/opcional). |
| `AIV_BLENDER_ASSETS_DIR` | `` (vacio) | Carpeta de assets 3D. Vacio = `media/assets3d`. |
| `AIV_BLENDER_SAMPLES` | `32` | Muestras de render (calidad vs tiempo). |
| `AIV_BLENDER_TIMEOUT_S` | `3600` | Timeout del subproceso de Blender por escena. |
| `AIV_PRODUCTION_TYPE` | `2d` | Default global de `production_type` para proyectos nuevos (`2d` \| `3d`). |
| `AIV_MEDIA_ROOT` | `./media` | Raiz de los workspaces por proyecto (clips, voz, timeline, MP4). |
| `AIV_FFMPEG_BIN` / `AIV_FFPROBE_BIN` | `ffmpeg` / `ffprobe` | Binarios de FFmpeg para mux/sondas. |
| `AIV_FPS` | `30` | Frames por segundo del clip y la composicion. |

Sin ninguna de estas variables el sistema sigue siendo el MVP 2D actual; `production_type`
decide por proyecto si se usa la fuente visual 3D.
