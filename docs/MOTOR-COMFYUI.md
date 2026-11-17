# BLOQUE 3 — Motor ComfyUI (imágenes IA locales, VRAM secuencial)

> Documento de diseño. Entregable de PLANIFICACIÓN: describe la arquitectura
> antes de implementar. Se alinea con `docs/ARCHITECTURE.md` (§3 capa de
> proveedores conmutable), `docs/LOCAL-FREE-STACK.md` (defaults locales/gratuitos
> con degradación garantizada) y `docs/EDITOR-ROADMAP.md` (BLOQUE 3).

## 0. Resumen ejecutivo

Se añade un backend **conmutable** del proveedor de imágenes: `ComfyUIImageProvider`,
que genera imágenes con **Stable Diffusion local** a través de la API HTTP de
**ComfyUI** (por defecto `http://localhost:8188`). Se registra en el registry bajo
el nombre `sd_local` y se activa con `AIV_IMAGE_BACKEND=sd_local`. Implementa el
mismo `Protocol ImageProvider` (`image_for(query, *, aspect, out_dir, seed) ->
ImageAsset`) que `stock` y `placeholder`, de modo que el `VisualEngine`, la API de
(re)generar imagen de escena y el pipeline funcionan **sin cambios** salvo el nuevo
registro.

Decisiones vinculantes del usuario que este diseño respeta:

- **Local y gratis.** ComfyUI es un servidor local; no hay APIs de pago. Los
  proveedores de pago (`stability`, `openai`) quedan como stubs conmutables
  intactos; no se eliminan ni se simplifican.
- **OFF por defecto.** `AIV_IMAGE_BACKEND` sigue siendo `stock` por defecto y
  `AIV_SD_LOCAL_ENABLED=false`. ComfyUI es opcional.
- **VRAM secuencial (RTX 3050 4 GB).** El pipeline genera el guion con Ollama,
  **libera la VRAM de Ollama** y solo después genera las imágenes con ComfyUI.
  Nunca los dos modelos cargados a la vez.
- **Degradación honesta.** Si ComfyUI no responde (sin servidor / sin red), se cae
  a `placeholder` (o al fallback configurado) con un log claro. **Nunca** se
  presenta una imagen falsa como una generación IA real.
- **Alcance: solo 2D.** Imágenes fijas por escena. No animación (eso es el motor
  3D / Blender o vídeo IA futuro).

---

## 1. Dónde encaja (arquitectura existente que se reutiliza, no se rehace)

| Pieza existente | Rol | Qué cambia en Bloque 3 |
|---|---|---|
| `backend/app/providers/base.py` → `Protocol ImageProvider`, `ImageAsset` | Contrato de la capacidad imagen | **Nada.** El nuevo provider lo implementa igual. |
| `backend/app/providers/registry.py` → `register`/`resolve`/`active_backend` | Resuelve el backend activo por `AIV_IMAGE_BACKEND` o por `Project.provider_overrides` | Se añade `register("image", "sd_local", ComfyUIImageProvider)` en `_register_builtin_providers()`. |
| `backend/app/providers/image/stock.py` | Provider real con httpx + degradación a `placeholder` | **Patrón de referencia.** No se toca. El nuevo provider lo imita (httpx, fallback). |
| `backend/app/providers/image/placeholder.py` | Red de seguridad (PNG local con Pillow, sin red) | **Nada.** Es el destino de la degradación de `sd_local`. |
| `backend/app/providers/image/aspect.py` → `aspect_to_dims` | `"16:9"` → `(ancho, alto)` | Se **reutiliza** para mapear aspect → width/height del latente. |
| `backend/app/engines/visual2d/engine.py` → `VisualEngine.render_scene_image` | Resuelve el provider, escribe el fichero, crea `Asset(kind='image')`, cache por hash `(visual_prompt, aspect, backend, seed)` | **Nada.** El `backend` (`sd_local`) ya entra en el hash de cache, así que no regenera si ya existe. |
| `backend/app/api/generation.py` → `POST /scenes/{id}/image` | (Re)genera imagen de escena | **Nada.** Funciona igual con `sd_local`. |
| `backend/app/providers/llm/ollama.py` → `OllamaLLM` | LLM local para el guion | Se añade un mecanismo de **unload** (liberar VRAM) invocable desde el pipeline. |
| `backend/app/jobs/pipeline.py` → `generate_video_handler` | Orquesta guion → imágenes/voz → … → render | Se añade un paso **explícito** de liberar VRAM de Ollama entre guion e imágenes, condicional. |
| `backend/app/config.py` → `Settings` | Lee `AIV_*` | Se añaden settings `AIV_SD_*` para ComfyUI (ya hay 3; se amplían). |

**Invariante central (§3):** cambiar de backend por variable de entorno NO toca
motores, API ni frontend. Este diseño lo preserva: todo el comportamiento nuevo
vive dentro del nuevo provider y de un paso condicional del pipeline.

---

## 2. La API de ComfyUI (flujo elegido)

ComfyUI expone una API HTTP (referencia: *ComfyUI Server Routes*). El flujo
elegido es **polling de `/history`**, el más simple y robusto para empezar (no
requiere mantener un websocket abierto):

1. **`POST /prompt`** con el workflow en *formato API* (`{"prompt": {grafo},
   "client_id": "<uuid>"}`). Responde `{"prompt_id": "...", "number": N}` o
   `{"error": ..., "node_errors": ...}` si la validación falla.
2. **`GET /history/{prompt_id}`** en bucle (polling con intervalo + timeout).
   Cuando el prompt termina, la respuesta contiene
   `history[prompt_id]["outputs"][<nodo SaveImage>]["images"]`, una lista de
   `{"filename": "...", "subfolder": "...", "type": "output"}`.
3. **`GET /view?filename=...&subfolder=...&type=output`** descarga el PNG. Se
   escribe en el workspace del proyecto.
4. **(VRAM)** `POST /free` con `{"unload_models": true, "free_memory": true}` para
   descargar los modelos de ComfyUI de la VRAM al terminar (simétrico al unload de
   Ollama; ver §5).
5. **(Diagnóstico, opcional)** `GET /system_stats` devuelve versión de Python,
   dispositivos y VRAM; útil para un futuro check de "ComfyUI vivo".

> **Websocket (`/ws`) descartado para el MVP.** Da progreso en tiempo real pero
> añade complejidad (conexión persistente, parseo de eventos binarios). El polling
> de `/history` cubre el caso "genera y espera el resultado" y es trivial de
> mockear en tests. Se documenta como mejora futura (progreso fino por nodo).

### 2.1 El workflow JSON (grafo de nodos)

El workflow es un grafo de nodos SD 1.5 clásico, parametrizado y **construido en
código** (función pura) a partir de `prompt / negative / width / height / seed /
steps / sampler / cfg / checkpoint`:

| Nodo | Clase ComfyUI | Parámetros que recibe |
|---|---|---|
| Checkpoint | `CheckpointLoaderSimple` | `ckpt_name` = `AIV_SD_CHECKPOINT` |
| Prompt positivo | `CLIPTextEncode` | `text` = prompt de la escena (+ estilo) |
| Prompt negativo | `CLIPTextEncode` | `text` = `AIV_SD_NEGATIVE_PROMPT` |
| Latente vacío | `EmptyLatentImage` | `width`, `height` (de `aspect_to_dims`), `batch_size=1` |
| Sampler | `KSampler` | `seed`, `steps`, `cfg`, `sampler_name`, `scheduler`, `denoise=1.0` |
| Decodificador | `VAEDecode` | latente del KSampler + VAE del checkpoint |
| Guardado | `SaveImage` | `filename_prefix` (p. ej. `aiv_scene`) |

Para no depender de una versión concreta del esquema de nodos, el grafo puede
venir de una **plantilla JSON configurable** (`AIV_SD_WORKFLOW_PATH`) con
marcadores (`{{prompt}}`, `{{negative}}`, `{{width}}`, `{{height}}`, `{{seed}}`,
`{{steps}}`, `{{cfg}}`, `{{sampler}}`, `{{scheduler}}`, `{{ckpt_name}}`) que el
provider sustituye. Si no se define plantilla, el provider usa un **grafo SD 1.5
por defecto embebido** (el de la tabla). Así el usuario avanzado puede exportar su
propio workflow desde la UI de ComfyUI ("Save (API format)") y enchufarlo sin
tocar código.

> La **construcción del grafo** y el **mapeo aspect → width/height** son lógica
> **pura** → se testean sin ComfyUI (ver §7). El resto (envío/polling/descarga) se
> testea con httpx mockeado.

### 2.2 Mapeo aspect → dimensiones del latente

Se reutiliza `aspect_to_dims` (lado largo 1920, dimensiones pares). **Matiz de
VRAM/SD 1.5:** SD 1.5 se entrenó a ~512 px; generar a 1920 px nativo es inviable
en 4 GB y degrada la calidad. El provider genera a una **resolución base reducida
y proporcional** (configurable con `AIV_SD_BASE_SIZE`, p. ej. lado largo 512–768,
múltiplos de 8) y registra en `meta` tanto la resolución generada como el aspect
objetivo. El escalado a la resolución final del vídeo lo hace la etapa de
composición (Remotion/FFmpeg), como ya ocurre con `stock`/`placeholder` cuyas
dimensiones tampoco son siempre exactas. El cálculo "aspect → base_size múltiplo de
8 manteniendo proporción" es puro y testeable.

---

## 3. `ComfyUIImageProvider` (diseño del provider)

Ubicación: `backend/app/providers/image/comfyui.py`. Implementa `ImageProvider`.

```text
name = "sd_local"

__init__(url, enabled, checkpoint, steps, sampler, scheduler, cfg, base_size,
         negative_prompt, workflow_path, lowvram, timeout, poll_interval,
         fallback_provider):
    - lee los valores de Settings si no se inyectan (patrón de StockImageProvider)
    - self._fallback = PlaceholderProvider()  (o el configurado por AIV_IMAGE_FALLBACK)

image_for(query, *, aspect, out_dir, seed) -> ImageAsset:
    1. si no está habilitado / URL vacía -> _fallback con log (degradación)
    2. width, height = base_size_for_aspect(aspect)           # puro
    3. graph = build_workflow(query, negative, width, height, # puro
                              seed, steps, sampler, scheduler, cfg, checkpoint)
    4. prompt_id = _submit(graph)           # POST /prompt
    5. images   = _poll_history(prompt_id)  # GET /history/{id} hasta outputs/timeout
    6. png = _download(images[0])           # GET /view?...
    7. escribe png en out_dir, crea ImageAsset(provider="sd_local", meta=...)
    8. (opcional) _free_vram()              # POST /free unload_models
    EN CUALQUIER error de red/validación/timeout -> _fallback con log claro
```

`ImageAsset.meta` incluye: `source="sd_local"`, `backend_engine="comfyui"`,
`checkpoint`, `sampler`, `scheduler`, `steps`, `cfg`, `seed`, `gen_width`,
`gen_height`, `aspect`, `license="depende-del-checkpoint"` (ver §8 riesgos).

### 3.1 Degradación (idéntico espíritu a `stock`)

`stock` cae a `placeholder` ante falta de clave/red/resultados. `sd_local` cae a
`placeholder` (o `AIV_IMAGE_FALLBACK`) ante: `AIV_SD_LOCAL_ENABLED=false`,
`ConnectError`/timeout (ComfyUI no está), `error`/`node_errors` del `/prompt`,
`/history` sin outputs tras el timeout, o `/view` vacío. **El `ImageAsset`
devuelto en degradación tiene `provider="placeholder"`** (nunca `sd_local`): el
Asset refleja la realidad y jamás se presenta un placeholder como imagen IA. Cada
degradación emite un `logger.warning` accionable (qué falló y la URL consultada).

### 3.2 Por qué NO rompe stock / placeholder / vía de pago / pipeline 2D-3D

- `stock.py` y `placeholder.py` **no se tocan**; `sd_local` es un registro
  adicional.
- Los stubs `stability`/`openai` siguen declarados y conmutables (no se añaden ni
  se quitan en este bloque).
- El pipeline 2D llama a `VisualEngine.render_scene_image`, que resuelve el
  provider por `active_backend("image", ...)`: con el default `stock` el
  comportamiento es idéntico al de hoy.
- El pipeline 3D (`production_type=='3d'`) usa la capacidad `clip` (Blender), no
  `image`; este bloque no lo afecta.
- La cache del `VisualEngine` ya incluye `backend` en el hash, así que cambiar a
  `sd_local` genera una entrada de cache nueva sin invalidar las de `stock`.

---

## 4. Settings nuevos (`AIV_SD_*`, patrón `AIV_`)

Se amplían los 3 ya existentes (`sd_local_enabled`, `sd_local_backend`,
`sd_local_url`) con los parámetros de generación. Todos con default seguro y
documentados en `.env.example`:

| Setting (`Settings`) | Env var | Default | Qué controla |
|---|---|---|---|
| `sd_local_enabled` | `AIV_SD_LOCAL_ENABLED` | `false` | Activa el provider (si `false`, degrada siempre). |
| `sd_local_backend` | `AIV_SD_LOCAL_BACKEND` | `comfyui` | Motor local (hoy solo `comfyui`). |
| `sd_local_url` | `AIV_SD_LOCAL_URL` | `http://localhost:8188` | URL del servidor ComfyUI. |
| `sd_checkpoint` | `AIV_SD_CHECKPOINT` | `v1-5-pruned-emaonly.safetensors` | Nombre del checkpoint SD cargado en ComfyUI. |
| `sd_steps` | `AIV_SD_STEPS` | `20` | Pasos del KSampler. |
| `sd_sampler` | `AIV_SD_SAMPLER` | `euler` | `sampler_name`. |
| `sd_scheduler` | `AIV_SD_SCHEDULER` | `normal` | `scheduler`. |
| `sd_cfg` | `AIV_SD_CFG` | `7.0` | CFG scale. |
| `sd_base_size` | `AIV_SD_BASE_SIZE` | `640` | Lado largo de la resolución de generación (SD 1.5). |
| `sd_negative_prompt` | `AIV_SD_NEGATIVE_PROMPT` | (texto por defecto) | Prompt negativo. |
| `sd_lowvram` | `AIV_SD_LOWVRAM` | `true` | Documental/diagnóstico (ComfyUI gestiona VRAM por flags de arranque; el provider lo registra en meta y lo documenta en el runbook). |
| `sd_workflow_path` | `AIV_SD_WORKFLOW_PATH` | `""` | Plantilla de workflow API opcional; vacío = grafo embebido. |
| `sd_timeout_s` | `AIV_SD_TIMEOUT_S` | `600` | Timeout total de generación por imagen. |
| `sd_poll_interval_s` | `AIV_SD_POLL_INTERVAL_S` | `1.5` | Intervalo de polling de `/history`. |
| `sd_free_after` | `AIV_SD_FREE_AFTER` | `true` | `POST /free` tras generar (libera VRAM). |
| `ollama_unload_between_stages` | `AIV_OLLAMA_UNLOAD_BETWEEN_STAGES` | `true` | Activa el paso de liberar VRAM de Ollama entre guion e imágenes (ver §5). |

`AIV_SD_LOWVRAM` no cambia la API (ComfyUI aplica `--lowvram` al arrancar el
servidor); se conserva como ajuste declarado + registrado en `meta` y referenciado
en el runbook de arranque de ComfyUI del usuario.

---

## 5. Gestión de VRAM secuencial (decisión del usuario)

Hardware objetivo: **RTX 3050 4 GB**. Ollama (modelo de guion) y ComfyUI (SD) no
caben a la vez. La cola de jobs ya es secuencial (un job a la vez), pero **dentro**
del pipeline `generate_video` ambos pasos ocurren en el mismo job, así que hay que
liberar explícitamente la VRAM de Ollama antes de generar imágenes.

### 5.1 Unload de Ollama (`OllamaLLM.unload()`)

Ollama descarga un modelo de la VRAM cuando recibe una petición de `generate` con
`keep_alive: 0`. El mecanismo elegido:

```text
OllamaLLM.unload():
    POST {ollama_url}/api/generate
      body = {"model": self.model, "keep_alive": 0}
    - tolerante: si Ollama no responde (ConnectError/timeout/status!=2xx) ->
      log.info("Ollama no disponible para unload; no-op") y return (NO lanza).
```

Es idempotente y seguro: pedir unload cuando Ollama ya no tiene el modelo cargado
no hace daño. Se testea con httpx mockeado (se verifica que se llama al endpoint
con `keep_alive: 0`) y el caso "Ollama caído → no-op sin excepción".

### 5.2 Paso en el pipeline (`jobs/pipeline.py`)

Entre el paso 1 (guion) y el paso 2 (imagen/voz por escena) se inserta un
sub-paso **condicional**:

```text
# tras _ensure_script(...) y antes de _generate_scene_assets(...)
if _should_unload_ollama_before_images(project):
    _release_llm_vram(project)   # OllamaLLM(...).unload(), tolerante
```

Condición `_should_unload_ollama_before_images(project)` (activa solo cuando tiene
sentido, para no tocar el modo por defecto que no usa GPU):

- `AIV_OLLAMA_UNLOAD_BETWEEN_STAGES` está en `true` (default), **y**
- el backend LLM activo es `ollama` (si es `openai` no hay VRAM local que liberar), **y**
- el backend de imagen activo es `sd_local` (si es `stock`/`placeholder` no hay GPU
  en juego; liberar sería inútil). En producción 3D (`clip`=Blender) el usuario
  puede optar por liberar igualmente, pero el default se centra en `sd_local`.

Resultado: en el **modo por defecto (stock/placeholder)** la condición es falsa y
el pipeline se comporta **exactamente igual que hoy** (ningún efecto, ninguna
llamada extra). Solo cuando el usuario activa ComfyUI entra en juego el unload. El
paso es tolerante: si Ollama ya se fue o no está, es un no-op con log, nunca rompe
el job.

Simetría: el `ComfyUIImageProvider` libera su propia VRAM con `POST /free` tras
generar (`AIV_SD_FREE_AFTER=true`), de modo que al volver a un siguiente job con
guion, Ollama vuelve a tener la VRAM libre.

---

## 6. Expectativas honestas de calidad / velocidad en 4 GB

- **SD 1.5 + lowvram:** viable pero **lento**. A `base_size=640`, ~20 steps, euler,
  cada imagen puede tardar de decenas de segundos a varios minutos según el equipo.
  Es un camino **opcional** para quien prioriza imágenes generadas sobre stock.
- **SDXL / Flux:** muy justo o **inviable** en 4 GB (SDXL pide ~8–12 GB cómodos;
  Flux más). No es el objetivo del bloque; si el usuario lo intenta, lo hace bajo su
  propio riesgo ajustando checkpoint + `base_size`.
- **Resolución:** se genera a resolución base reducida (SD 1.5) y se escala en
  composición. No esperar nitidez 1080p nativa de SD 1.5.
- **Default sigue siendo stock/placeholder:** rápido, sin GPU, sin descargas.
  ComfyUI es un extra para quien ya lo tiene montado.

---

## 7. Estrategia de pruebas (sandbox SIN ComfyUI ni GPU)

ComfyUI **no** está instalado en el sandbox y no hay GPU. El provider debe (a)
funcionar de verdad en el PC del usuario y (b) ser **testeable aquí sin ComfyUI**:

- **Lógica pura (sin red):** `build_workflow(...)` y `base_size_for_aspect(...)` se
  testean con asserts sobre el grafo (nodos presentes, parámetros sustituidos,
  dimensiones múltiplos de 8, seed/steps/cfg correctos). Patrón: tests del motor 3D
  (`test_mapping.py`, `test_script_builder.py`).
- **Red mockeada (patrón `test_stock_provider.py`):** `monkeypatch` de
  `app.providers.image.comfyui.httpx` para simular `POST /prompt` → `prompt_id`,
  `GET /history/{id}` → outputs, `GET /view` → bytes PNG; se verifica que el
  `ImageAsset` resultante tiene `provider="sd_local"`, el PNG en disco y la meta
  esperada. **Ninguna llamada real.**
- **Degradación:** con `enabled=false`, con `ConnectError`, con `/history` sin
  outputs (timeout corto en test) y con `node_errors` → el provider devuelve un
  `ImageAsset` con `provider="placeholder"` y un PNG real. Mismo estilo que
  `test_stock_degrades_to_placeholder_*`.
- **Unload de Ollama:** `OllamaLLM.unload()` con httpx mockeado verifica el `POST
  /api/generate` con `keep_alive: 0`; y el caso "Ollama caído → no-op sin
  excepción".
- **Paso del pipeline:** test con el backend de imagen a `sd_local` (provider fake)
  y un spy sobre el unload que confirma que se invoca **una vez** entre guion e
  imágenes; y test con el backend por defecto (`stock`) que confirma que el unload
  **no** se invoca (el modo por defecto no cambia). Patrón: `test_pipeline_*.py`.

Comando de verificación backend (igual que los bloques previos):

```bash
cd backend && . .venv/bin/activate && \
  AIV_FFMPEG_BIN=/projects/sandbox/.localbin/ffmpeg \
  AIV_FFPROBE_BIN=/projects/sandbox/.localbin/ffprobe pytest -q
```

---

## 8. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| **ComfyUI no instalable en el sandbox / sin GPU.** | El provider es 100 % mockeable: lógica pura + httpx mockeado + degradación. La validación real la hace el usuario en su PC (runbook). Los tests que tocarían ComfyUI real no existen; se cubren con mocks. |
| **VRAM 4 GB insuficiente.** | VRAM secuencial (unload de Ollama + `/free` de ComfyUI), `base_size` reducido, `lowvram` documentado, SD 1.5 (no SDXL/Flux). Expectativas honestas documentadas (§6). |
| **Licencias de los checkpoints de SD.** | El checkpoint lo aporta y lo elige el usuario; cada modelo (p. ej. SD 1.5, derivados) tiene su propia licencia (CreativeML OpenRAIL-M u otras) y restricciones de uso. **Responsabilidad del usuario.** El provider registra `checkpoint` en `meta` para trazabilidad y lo documenta como responsabilidad del usuario (no se empaqueta ningún checkpoint en el repo). |
| **El esquema de nodos de ComfyUI cambia entre versiones.** | El workflow es una **plantilla configurable** (`AIV_SD_WORKFLOW_PATH`): el usuario exporta su grafo desde ComfyUI ("Save API format") si el embebido no encaja con su versión/nodos. |
| **Generación lenta bloquea el job.** | Timeout configurable (`AIV_SD_TIMEOUT_S`); al agotarse, degrada a placeholder con log (el vídeo se produce igual). La cola es secuencial, así que no se solapan jobs. |
| **Imagen simulada presentada como real.** | Prohibido. En degradación el Asset es `provider="placeholder"`, nunca `sd_local`. |

---

## 9. Qué queda fuera de este bloque (no-objetivos)

- Animación / vídeo IA (es motor 3D/Blender o futuro).
- img2img, inpainting, ControlNet, LoRAs (posible evolución; el workflow
  configurable deja la puerta abierta sin comprometerse aquí).
- Progreso fino por websocket (`/ws`); se usa polling de `/history`.
- Instalar o empaquetar ComfyUI o checkpoints en el repo.
- UI de selección de backend de imágenes: **opcional** (feature independiente al
  final del bloque); el núcleo funciona por variable de entorno sin tocar frontend.

---

## 10. Trazabilidad con otros documentos

- `docs/ARCHITECTURE.md` §3 — capa de proveedores conmutable (registry, degradación).
- `docs/LOCAL-FREE-STACK.md` — tabla de capacidades (imágenes: `sd_local` como
  alternativa pluggable), degradación garantizada, restricciones del sandbox.
- `docs/EDITOR-ROADMAP.md` — BLOQUE 3 (este documento lo materializa).
- `docs/RISKS-ASSUMPTIONS.md` — riesgos de VRAM/GPU en el sandbox.
