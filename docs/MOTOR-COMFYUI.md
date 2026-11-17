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
- **Degradación honesta.** Si ComfyUI no responde (sin servidor / sin red) o
  cualquiera de los fallos de la taxonomía de §6 ocurre, se cae a `placeholder` (o
  al fallback configurado) con un log claro y con `meta.status="fallback"` +
  `meta.fallback_reason=<categoría>` en el Asset. **Nunca** se presenta una imagen
  falsa como una generación IA real (el Asset degradado es `provider="placeholder"`,
  jamás `sd_local`).
- **Concurrencia controlada.** Una sola generación SD a la vez (lock/semáforo de
  provider, `AIV_SD_MAX_CONCURRENCY=1`, ampliable por config) para no saturar los
  4 GB de VRAM. Reutiliza la cola de jobs secuencial existente sin duplicarla.
- **Memoria verificada.** No se asume que `keep_alive=0` baste: antes de generar
  con ComfyUI se **verifica** que Ollama liberó el modelo (consulta a `/api/ps` con
  espera/reintento acotado y log del resultado), y ComfyUI administra su VRAM con
  `/free`.
- **Alcance: solo 2D.** Imágenes fijas por escena. No animación (eso es el motor
  3D / Blender o vídeo IA futuro).

> **Revisión técnica (7 ajustes del usuario).** Este documento incorpora:
> (1) concurrencia/cola de generación, (2) administración de memoria con
> verificación de unload de Ollama, (3) taxonomía de errores y recuperación,
> (4) metadatos de generación en `Asset.meta` sin migración de esquema,
> (5) preparación (solo diseño) para consistencia visual, (6) protección de la
> arquitectura existente y regresión verde, y (7) ejecución doc-primero. Las
> secciones §5, §6, §8, §8bis y §9bis materializan estos puntos.

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
| `backend/app/providers/llm/ollama.py` → `OllamaLLM` | LLM local para el guion | Se añade **unload** (liberar VRAM) y **verificación** del unload vía `/api/ps` (§5.1), invocables desde el pipeline. |
| `backend/app/jobs/pipeline.py` → `generate_video_handler` | Orquesta guion → imágenes/voz → … → render | Se añade un paso **explícito y condicional** de liberar + verificar VRAM de Ollama entre guion e imágenes. |
| `backend/app/jobs/queue.py` → `JobQueue` (cola secuencial) | `claim()` reclama 1 job pendiente a la vez | **Nada.** Se reutiliza; el límite "1 generación SD a la vez" se garantiza con un semáforo de provider complementario (§5bis), sin duplicar la cola. |
| `backend/app/config.py` → `Settings` | Lee `AIV_*` | Se añaden settings `AIV_SD_*` (incl. `AIV_SD_MAX_CONCURRENCY`) y `AIV_OLLAMA_UNLOAD_*` (ya hay 3 de SD; se amplían). |

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
    0. si no está habilitado / URL vacía -> _fallback (status=fallback,
                                            fallback_reason="disabled") con log
    1. adquirir SEMÁFORO de concurrencia (§5bis); todo lo demás en try/finally
    2. t0 = time.monotonic()
    3. width, height = base_size_for_aspect(aspect)           # puro
    4. graph = build_workflow(query, negative, width, height, # puro
                              seed, steps, sampler, scheduler, cfg, checkpoint,
                              style_refs=None, char_refs=None) # §10bis (futuro)
    5. (opcional) _free_vram()  si sd_free_before             # POST /free
    6. con reintentos controlados SOLO para categorías transitorias (§6):
         prompt_id = _submit(graph)           # POST /prompt
         images    = _poll_history(prompt_id) # GET /history/{id} hasta outputs/timeout
         png       = _download(images[0])     # GET /view?...  (valida con PIL)
    7. meta = build_generation_meta(provider="sd_local", model=checkpoint,
                 prompt=query, negative_prompt=negative, seed=seed, width, height,
                 steps, sampler, cfg, generation_time_s=now-t0, status="ok")
    8. crea ImageAsset(provider="sd_local", meta=meta)
    9. (opcional) _free_vram()  si sd_free_after              # POST /free
   10. EN CUALQUIER ComfyUIError (tras agotar reintentos) -> _fallback con
       status=fallback, fallback_reason=<categoría>, log accionable
   finally: liberar SEMÁFORO
```

`ImageAsset.meta` lleva el **shape canónico de metadatos de generación** definido
en §8bis (`provider`, `model`/`checkpoint`, `prompt`, `negative_prompt`, `seed`,
`width`/`height`, `steps`, `sampler`, `cfg`, `generation_time_s`, `status`,
`fallback_reason`) más `backend_engine="comfyui"`, `scheduler`, `gen_width`/
`gen_height` (resolución real de generación SD) y `license="depende-del-checkpoint"`
(ver §9 riesgos). **Este `meta` se persiste tal cual en `Asset.meta` sin tocar el
esquema** (ver §8bis: estudio del modelo real).

### 3.1 Degradación honesta (idéntico espíritu a `stock`, con taxonomía)

`stock` cae a `placeholder` ante falta de clave/red/resultados. `sd_local` cae a
`placeholder` (o `AIV_IMAGE_FALLBACK`) ante **cualquiera** de las categorías de
error de §6 (conexión, checkpoint inexistente, workflow inválido, timeout,
CUDA/OOM, imagen corrupta) o con `AIV_SD_LOCAL_ENABLED=false`. **El `ImageAsset`
devuelto en degradación tiene `provider="placeholder"`** (nunca `sd_local`), con
`meta.status="fallback"` y `meta.fallback_reason=<categoría>`: el Asset refleja la
realidad y jamás se presenta un placeholder como imagen IA. Cada degradación emite
un `logger.warning` accionable (categoría + URL consultada). La adquisición del
lock de concurrencia (§5bis) se libera en `finally`, también en degradación.

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
| `sd_connect_timeout_s` | `AIV_SD_CONNECT_TIMEOUT_S` | `5.0` | Timeout de conexión (detecta rápido "ComfyUI no está"). |
| `sd_max_retries` | `AIV_SD_MAX_RETRIES` | `1` | Reintentos SOLO de categorías transitorias (conexión/timeout), con backoff (§6). |
| `sd_free_before` | `AIV_SD_FREE_BEFORE` | `false` | `POST /free` antes de generar (libera VRAM previa). |
| `sd_free_after` | `AIV_SD_FREE_AFTER` | `true` | `POST /free` tras generar (libera VRAM). |
| `sd_max_concurrency` | `AIV_SD_MAX_CONCURRENCY` | `1` | Generaciones SD simultáneas (semáforo de provider, §5bis). Ampliable por config. |
| `ollama_unload_between_stages` | `AIV_OLLAMA_UNLOAD_BETWEEN_STAGES` | `true` | Activa el paso de liberar VRAM de Ollama entre guion e imágenes (ver §5). |
| `ollama_unload_verify` | `AIV_OLLAMA_UNLOAD_VERIFY` | `true` | Verifica el unload consultando `/api/ps` (no asume `keep_alive=0`; §5). |
| `ollama_unload_timeout_s` | `AIV_OLLAMA_UNLOAD_TIMEOUT_S` | `30.0` | Espera máxima para confirmar la descarga en `/api/ps`. |
| `ollama_unload_poll_interval_s` | `AIV_OLLAMA_UNLOAD_POLL_INTERVAL_S` | `1.0` | Intervalo de sondeo de `/api/ps`. |

`AIV_SD_LOWVRAM` no cambia la API (ComfyUI aplica `--lowvram` al arrancar el
servidor); se conserva como ajuste declarado + registrado en `meta` y referenciado
en el runbook de arranque de ComfyUI del usuario.

---

## 5. Gestión de VRAM secuencial (decisión del usuario)

Hardware objetivo: **RTX 3050 4 GB**. Ollama (modelo de guion) y ComfyUI (SD) no
caben a la vez. La cola de jobs ya es secuencial (un job a la vez), pero **dentro**
del pipeline `generate_video` ambos pasos ocurren en el mismo job, así que hay que
liberar explícitamente la VRAM de Ollama antes de generar imágenes.

### 5.1 Unload de Ollama **verificado** (`OllamaLLM.unload()` + `verify_unloaded()`)

Ollama descarga un modelo de la VRAM cuando recibe una petición de `generate` con
`keep_alive: 0`. Pero **no se asume que eso baste** (punto 2): la descarga puede
tardar, y hasta que la VRAM no está libre de verdad, arrancar ComfyUI en 4 GB
puede fallar por OOM. Por eso el unload se **verifica** consultando `/api/ps`
(endpoint de Ollama que lista los modelos actualmente cargados):

```text
OllamaLLM.unload():
    POST {ollama_url}/api/generate  body = {"model": self.model, "keep_alive": 0}
    - tolerante: si Ollama no responde (ConnectError/timeout/status!=2xx) ->
      log.info("Ollama no disponible para unload; no-op") y return (NO lanza).

OllamaLLM.loaded_models() -> list[str]:
    GET {ollama_url}/api/ps -> [m["name"]/m["model"] for m in data["models"]]
    - tolerante: si falla o el endpoint no existe -> [] + log.info.

OllamaLLM.verify_unloaded(timeout_s, poll_interval_s) -> bool:
    bucle sondeando /api/ps hasta que self.model ya NO figure cargado:
      - confirmado -> log.info("Ollama liberó el modelo X") y return True
      - expira timeout_s -> log.warning("Ollama no confirmó la descarga de X
        tras Ns; se continúa igualmente") y return False
    - NUNCA lanza; si /api/ps no existe en esa versión, degrada con log y continúa.
```

`unload()` es idempotente y seguro. `verify_unloaded()` **informa y no bloquea**:
su objetivo es dejar traza honesta (log) de si la VRAM quedó realmente libre, sin
romper el pipeline si Ollama es de una versión sin `/api/ps`. Mismo patrón que el
`_ollama_reachable` ya existente en `pipeline.py` (que hace `GET /api/tags`). Se
testea con httpx mockeado: `/api/ps` que primero lista el modelo y luego no
(→ `True`), `/api/ps` que sigue listándolo hasta expirar (→ `False` sin lanzar), y
`/api/ps` caído (→ degrada a `[]`).

### 5.2 Paso en el pipeline (`jobs/pipeline.py`)

Entre el paso 1 (guion) y el paso 2 (imagen/voz por escena) se inserta un
sub-paso **condicional**:

```text
# tras _ensure_script(...) y antes de _generate_scene_assets(...)
if _should_unload_ollama_before_images(project):
    _release_llm_vram(project)
    # _release_llm_vram = OllamaLLM(...).unload() y, si AIV_OLLAMA_UNLOAD_VERIFY,
    # .verify_unloaded(timeout_s, poll_interval_s). Todo tolerante.
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

Simetría: el `ComfyUIImageProvider` libera su propia VRAM con `POST /free` antes
(`AIV_SD_FREE_BEFORE`, opcional) y/o después de generar (`AIV_SD_FREE_AFTER=true`),
de modo que al volver a un siguiente job con guion, Ollama vuelve a tener la VRAM
libre. Si `/free` no existe en la versión de ComfyUI del usuario, la llamada es
**tolerante** (log + continuar).

---

## 5bis. Concurrencia: cola de generación y lock de provider (decisión del usuario)

**Problema (punto 1):** con 4 GB de VRAM, dos generaciones SD a la vez saturan la
GPU. Hay que garantizar **una sola generación simultánea**, con la puerta abierta a
ampliar por configuración.

**Qué ya existe (estudiado en el código):** `jobs/queue.py` implementa una cola
respaldada por la tabla `Job`; `JobQueue.claim()` reclama **el job pendiente más
antiguo** y lo marca `running` (uno a la vez), y el `worker.py` por defecto procesa
los jobs de forma secuencial. **Pero** `generate_video_handler` ejecuta el guion y
**todas** las imágenes de las escenas dentro del **mismo** job, en un bucle: la cola
de jobs serializa *jobs completos*, no *generaciones de imagen individuales*.

**Decisión (sin duplicar la cola):** NO se crea una segunda cola. Se añade un
**semáforo/lock a nivel de módulo** en `comfyui.py`
(`threading.BoundedSemaphore(AIV_SD_MAX_CONCURRENCY)`, construido perezosamente; con
el default `1` equivale a un lock). `image_for` lo adquiere antes de `_submit` y lo
libera en `finally` (también en degradación). Así:

- Dentro de un job, las escenas se generan **de una en una** aunque el bucle las
  recorra en orden (el semáforo serializa el tramo de red SD).
- Si en el **futuro** se ejecutan varios workers/hilos (Fase 4), el invariante
  "nunca 2 generaciones SD a la vez" **sigue garantizado** por el semáforo de
  provider, sin tocar la cola de jobs ni los motores.
- **Ampliable por config:** subir `AIV_SD_MAX_CONCURRENCY` (p. ej. a 2 en una GPU
  mayor) relaja el límite sin cambios de código.

La cola de jobs existente y el semáforo de provider son **complementarios**: la
cola ordena el trabajo de alto nivel (reanudable, con reintentos por escena); el
semáforo protege el recurso escaso (VRAM) en el punto exacto de la generación. Se
testea con hilos de Python + un `Event` que bloquea `_submit`, midiendo que la
concurrencia observada nunca supere `AIV_SD_MAX_CONCURRENCY` y que el lock se libere
también tras un fallo (ver §7).

---

## 5ter. Taxonomía de errores y recuperación (decisión del usuario, punto 3)

El provider **diferencia** categorías de fallo, con su manejo (timeout, reintentos,
degradación) y su `fallback_reason` para trazabilidad honesta en `meta`. Se modela
con una jerarquía de excepciones internas (`ComfyUIError` base) que `image_for`
captura y traduce a degradación:

| Categoría | Excepción interna | Cómo se detecta | ¿Reintenta? | `fallback_reason` |
|---|---|---|---|---|
| Deshabilitado | (n/a) | `AIV_SD_LOCAL_ENABLED=false` o URL vacía | (n/a) | `disabled` |
| Conexión | `ComfyUIConnectionError` | `httpx.ConnectError`/`ConnectTimeout` (ComfyUI no está) | **Sí** (transitorio) | `connection` |
| Timeout | `ComfyUITimeoutError` | `/history` sin outputs tras `sd_timeout_s`; `httpx.TimeoutException` | **Sí** (transitorio) | `timeout` |
| Checkpoint inexistente | `ComfyUICheckpointError` | `node_errors`/`error` de `/prompt` que menciona `ckpt`/checkpoint no encontrado | **No** | `checkpoint_missing` |
| Workflow inválido | `ComfyUIWorkflowError` | `node_errors`/validación de `/prompt` (grafo mal formado) | **No** | `invalid_workflow` |
| CUDA / OOM | `ComfyUICudaError` | error de ejecución en `/history` cuyo texto contiene `CUDA`/`out of memory` | **No** | `cuda_oom` |
| Imagen corrupta | `ComfyUIImageError` | `/view` vacío o bytes no decodificables por Pillow (`Image.open`/`verify`) | **No** | `corrupt_image` |

Reglas:

- **Timeouts:** conexión acotada por `AIV_SD_CONNECT_TIMEOUT_S` (detecta rápido
  "no está"); generación total por `AIV_SD_TIMEOUT_S`.
- **Reintentos controlados:** solo categorías **transitorias** (conexión/timeout)
  se reintentan, hasta `AIV_SD_MAX_RETRIES` con backoff exponencial y tope.
  Reintentar un checkpoint inexistente, un workflow inválido o un OOM no ayuda y
  gasta VRAM, así que esas categorías **no** se reintentan.
- **Degradación honesta:** agotados los reintentos (o categoría no transitoria), se
  cae a `placeholder` con `meta.status="fallback"` y `meta.fallback_reason` = la
  categoría. El Asset es `provider="placeholder"`, nunca `sd_local`. `image_for`
  **nunca** lanza hacia el pipeline.
- **Validación de la imagen:** la descarga de `/view` se valida con Pillow antes de
  aceptarse; una imagen corrupta degrada en vez de escribir basura en disco.

Cada categoría tiene un test dedicado con httpx mockeado (§7).

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
- **Degradación y taxonomía (§5ter):** una prueba por **categoría** de error
  (`disabled`, `connection`, `timeout`, `checkpoint_missing`, `invalid_workflow`,
  `cuda_oom`, `corrupt_image`); en cada una el provider devuelve un `ImageAsset`
  con `provider="placeholder"`, `meta.status="fallback"` y el `meta.fallback_reason`
  esperado, con un PNG real, y nunca lanza. Más una prueba de **reintentos**: un
  `connection` transitorio que luego tiene éxito acaba en `sd_local`; un
  `checkpoint_missing` **no** reintenta (se cuenta `_submit`). Mismo estilo que
  `test_stock_degrades_to_placeholder_*`.
- **Metadatos (§8bis):** `build_generation_meta(...)` (puro) devuelve el shape
  canónico con las claves ausentes en `None`; y el test de éxito afirma que el
  `meta` del Asset lleva `status="ok"`, `model`/`checkpoint`, `prompt`,
  `negative_prompt`, `seed`, `width`/`height`, `steps`, `sampler`, `cfg`,
  `generation_time_s`.
- **Concurrencia (§5bis):** `test_comfyui_concurrency.py` lanza 2 `image_for` en
  hilos con `_submit` bloqueado en un `Event` y afirma concurrencia máxima
  observada `<= AIV_SD_MAX_CONCURRENCY` (1 por defecto; 2 al reconfigurar), y que
  el lock se libera también tras un fallo.
- **Unload + verificación de Ollama (§5.1):** `OllamaLLM.unload()` con httpx
  mockeado verifica el `POST /api/generate` con `keep_alive: 0`; `verify_unloaded()`
  verifica `GET /api/ps` que deja de listar el modelo (→ `True`), que lo sigue
  listando hasta expirar (→ `False` sin lanzar) y `/api/ps` caído (→ degrada a
  `[]`); y el caso "Ollama caído → no-op sin excepción".
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

## 8bis. Metadatos de generación en `Asset.meta` (estudio del esquema, punto 4)

**Estudio previo del modelo real (obligatorio antes de tocar metadatos):**

- `backend/app/models/entities.py` → clase `Asset`: campos `project_id`,
  `scene_id`, `kind`, `path`, `provider`, **`meta: Mapped[dict | None] =
  mapped_column(JSON, default=None)`**, `created_at`. El `meta` es una columna
  **`JSON` portable** (SQLite ahora, Postgres en Fase 4), es decir **un dict JSON
  libre**.
- `backend/app/providers/base.py` → dataclass `ImageAsset`: `path`, `width`,
  `height`, `provider`, `query`, `seed`, y **`meta: dict = field(default_factory=
  dict)`**.
- `backend/app/engines/visual2d/engine.py` → `VisualEngine.render_scene_image` ya
  hace `meta = dict(image.meta or {})` y lo **amplía** con
  `cache_key/aspect/backend/seed/width/height/query` antes de crear el
  `Asset(... meta=meta)`.

**Conclusión (confirmada en código): los metadatos de generación caben SIN
migración de esquema.** No se añade ni se modifica ninguna columna ni ningún
modelo; solo se **amplían las claves** del dict `meta` que ya se persiste. Esto
respeta "no modificar innecesariamente el esquema actual de Assets".

**Shape canónico** (`build_generation_meta(...)`, función pura y testeable). Claves
no disponibles → `None` (nunca se inventan valores):

```json
{
  "provider": "sd_local",
  "model": "v1-5-pruned-emaonly.safetensors",
  "prompt": "<visual_prompt (+estilo)>",
  "negative_prompt": "<AIV_SD_NEGATIVE_PROMPT>",
  "seed": 12345,
  "width": 640,
  "height": 360,
  "steps": 20,
  "sampler": "euler",
  "cfg": 7.0,
  "generation_time_s": 42.7,
  "status": "ok",
  "fallback_reason": null
}
```

Más claves auxiliares del provider: `backend_engine="comfyui"`, `scheduler`,
`gen_width`/`gen_height` (resolución **real** de generación SD, por si difiere del
`width`/`height` objetivo del aspect). En degradación: `provider` del meta queda
`"sd_local"` como intento, pero el **`Asset.provider`** y `ImageAsset.provider` son
`"placeholder"`, con `status="fallback"` y `fallback_reason=<categoría>`, de modo
que queda registrado que se *intentó* SD y por qué se cayó, sin presentar el
placeholder como imagen IA.

> **Matiz de integración (para FEAT-004):** `VisualEngine` sobrescribe
> `meta["seed"]/["width"]/["height"]` con los valores de entrada/`ImageAsset`. Por
> eso la resolución **real** de generación SD se conserva aparte bajo
> `gen_width`/`gen_height`, evitando colisión con el `width`/`height` del aspect
> objetivo.

---

## 9. Qué queda fuera de este bloque (no-objetivos)

- Animación / vídeo IA (es motor 3D/Blender o futuro).
- img2img, inpainting, ControlNet, LoRAs (posible evolución; el workflow
  configurable y los puntos de extensión de §10bis dejan la puerta abierta sin
  comprometerse aquí).
- **Consistencia visual implementada:** referencias de personajes/escenarios,
  estilos reutilizables y prompts por personaje/escena **NO se implementan** en este
  bloque; solo se deja la arquitectura **preparada** (§10bis, punto 5).
- Progreso fino por websocket (`/ws`); se usa polling de `/history`.
- Instalar o empaquetar ComfyUI o checkpoints en el repo.
- UI de selección de backend de imágenes: **opcional** (feature independiente al
  final del bloque); el núcleo funciona por variable de entorno sin tocar frontend.

---

## 9bis. Protección de la arquitectura y regresión (decisión del usuario, punto 6)

Invariantes que **cada feature** del plan refuerza en sus criterios de aceptación:

- **`stock` sigue siendo el backend por defecto** (`AIV_IMAGE_BACKEND=stock`);
  `placeholder` es la alternativa/red de seguridad. `sd_local` es un registro
  **adicional** en el registry.
- **Con `sd_local` desactivado** (`AIV_SD_LOCAL_ENABLED=false` o backend por
  defecto) **el comportamiento actual NO cambia** (sin regresión): el paso de
  unload del pipeline queda inactivo, el semáforo no entra en juego y el flujo es
  idéntico al de hoy.
- **No se tocan los proveedores de pago** (`stability`/`openai` stubs) ni se
  simplifican; siguen declarados y conmutables.
- **No se afecta Blender ni el pipeline 3D** (capacidad `clip`): la producción 3D
  usa `clip`, no `image`; este bloque no la toca.
- **`stock.py` y `placeholder.py` no se modifican**; `sd_local` los imita pero vive
  en su propio módulo.
- **Regresión verde:** la baseline de los Bloques 0/1/2 (**~456 passed / 3 skipped**,
  fijada por FEAT-001) debe seguir verde al cerrar el bloque; el conteo solo crece
  por los tests nuevos de ComfyUI/concurrencia/VRAM. Ningún test existente puede
  pasar a fallar ni a skip.

Estos invariantes aparecen como `acceptance_criteria` en FEAT-004 (integración) y
FEAT-001 (baseline), y como restricción transversal en el resto.

---

## 10bis. Puntos de extensión para consistencia visual (punto 5, SOLO diseño)

Se deja la arquitectura **preparada** para incorporar más adelante, **sin
implementarlo ahora** (no es código, es diseño extensible):

- **Referencias de personajes / escenarios:** `build_workflow(...)` acepta ya
  parámetros opcionales `style_refs` y `char_refs` (default `None`, ignorados hoy).
  Son el enganche para, en el futuro, inyectar nodos de referencia (p. ej.
  ControlNet / IP-Adapter / img2img) o imágenes de referencia por personaje/set.
- **Estilos artísticos reutilizables:** hoy el estilo llega como texto concatenado
  al prompt (`VisualEngine.render_scene_image(style=...)`). Punto de extensión
  futuro: un "style" **resoluble** (biblioteca de estilos con nombre →
  prompt/negative/LoRA/parámetros), sin comprometer su forma aquí.
- **Prompts por personaje/escena:** el `Scene.visual_prompt` ya es por escena. La
  extensión futura sería una capa que componga el prompt final a partir de un perfil
  de personaje + un perfil de escena + el estilo, resuelta **antes** de
  `build_workflow`.
- **Plantilla de workflow configurable** (`AIV_SD_WORKFLOW_PATH`): ya permite al
  usuario avanzado enchufar grafos con nodos de referencia sin tocar código.

Estos enganches no añaden comportamiento en este bloque; existen para que la
evolución no exija reescribir el provider.

---

## 10. Trazabilidad con otros documentos

- `docs/ARCHITECTURE.md` §3 — capa de proveedores conmutable (registry, degradación).
- `docs/LOCAL-FREE-STACK.md` — tabla de capacidades (imágenes: `sd_local` como
  alternativa pluggable), degradación garantizada, restricciones del sandbox.
- `docs/EDITOR-ROADMAP.md` — BLOQUE 3 (este documento lo materializa).
- `docs/RISKS-ASSUMPTIONS.md` — riesgos de VRAM/GPU en el sandbox.
