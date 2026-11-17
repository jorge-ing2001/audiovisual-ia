"""``ComfyUIImageProvider`` — generación local de imágenes con Stable Diffusion.

Motor del Bloque 3 (ver ``docs/MOTOR-COMFYUI.md``). Habla con un servidor ComfyUI
local vía su API HTTP: encola un workflow (grafo de nodos), espera a que termine y
descarga la imagen resultante. Pensado para una RTX 3050 de 4 GB (SD 1.5 en
``--lowvram``, latente pequeña + upscale posterior, nunca dos generaciones a la vez).

Este módulo contiene DOS capas:

1. **Lógica pura (sin red, 100% testeable):** construcción del grafo del workflow
   (:func:`build_workflow`), mapeo aspecto→dimensiones de la latente
   (:func:`base_size_for_aspect`), identificación/versionado del workflow
   (:func:`workflow_version`, :func:`workflow_hash`) y el *shape* canónico del meta
   de generación (:func:`build_generation_meta`). Más :class:`ComfyUICapabilities`,
   la estructura de detección de capacidades por versión del servidor.
2. **Provider (parte de red):** :class:`ComfyUIImageProvider`, que en FEAT-003 habla
   con el servidor vía ``httpx`` (encolar en ``/prompt``, esperar en ``/history``,
   descargar en ``/view`` y liberar VRAM en ``/free``), valida el PNG con Pillow,
   aplica una TAXONOMIA DE ERRORES (:class:`ComfyUIError` y subclases) con reintentos
   controlados SOLO de categorías transitorias y degrada honestamente a
   ``PlaceholderProvider``.

``httpx`` se importa a nivel de módulo (como en ``stock.py``) para que los tests de
red de FEAT-003 puedan mockear ``app.providers.image.comfyui.httpx``.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from app.config import get_settings
from app.providers.base import ImageAsset
from app.providers.image.aspect import aspect_to_dims
from app.providers.image.placeholder import PlaceholderProvider

logger = logging.getLogger(__name__)

# Identificador del grafo SD 1.5 embebido. Cualquier cambio estructural del grafo
# embebido debe subir esta versión (para invalidar caches / registrar compatibilidad).
EMBEDDED_WORKFLOW_VERSION = "sd15-embedded-v1"

# Marcadores admitidos en una plantilla JSON externa (AIV_SD_WORKFLOW_PATH).
_TEMPLATE_PLACEHOLDERS = (
    "prompt",
    "negative",
    "width",
    "height",
    "seed",
    "steps",
    "cfg",
    "sampler",
    "scheduler",
    "ckpt_name",
)


# --------------------------------------------------------------------------- #
# Taxonomía de errores (punto 3)
# --------------------------------------------------------------------------- #


class ComfyUIError(Exception):
    """Error base de la conversación con ComfyUI.

    Cada subclase categoriza un modo de fallo y expone un ``fallback_reason``
    legible que viaja al ``meta`` del Asset degradado (trazabilidad honesta). El
    atributo :attr:`transient` marca si reintentar puede ayudar (conexión/timeout)
    o no (checkpoint/workflow/CUDA): las categorías no transitorias no se reintentan
    porque reintentar no ayuda y gasta VRAM.
    """

    fallback_reason = "comfyui_error"
    transient = False


class ComfyUIConnectionError(ComfyUIError):
    """No se pudo contactar con el servidor ComfyUI.

    Se detecta al capturar ``httpx.ConnectError`` / ``httpx.ConnectTimeout`` en
    cualquier llamada HTTP (``_submit``/``_poll_history``/``_download``). Transitoria.
    """

    fallback_reason = "connection"
    transient = True


class ComfyUICheckpointError(ComfyUIError):
    """El checkpoint (modelo .safetensors/.ckpt) no existe en el servidor.

    Se detecta en :meth:`ComfyUIImageProvider._submit` cuando la respuesta de
    ``/prompt`` trae ``error``/``node_errors`` cuyo texto menciona checkpoint/ckpt.
    NO transitoria (reintentar no lo instala).
    """

    fallback_reason = "checkpoint_missing"
    transient = False


class ComfyUIWorkflowError(ComfyUIError):
    """El grafo del workflow es inválido (validación del servidor).

    Se detecta en :meth:`ComfyUIImageProvider._submit` ante ``error``/``node_errors``
    de validación genéricos (no relacionados con el checkpoint). NO transitoria.
    """

    fallback_reason = "invalid_workflow"
    transient = False


class ComfyUITimeoutError(ComfyUIError):
    """Se agotó el tiempo esperando el resultado.

    Se detecta en :meth:`ComfyUIImageProvider._poll_history` al superar
    ``sd_timeout_s`` sin salida, o al capturar ``httpx.TimeoutException`` general.
    Transitoria.
    """

    fallback_reason = "timeout"
    transient = True


class ComfyUICudaError(ComfyUIError):
    """Error de GPU durante la ejecución del nodo (CUDA / out of memory).

    Se detecta en :meth:`ComfyUIImageProvider._poll_history` cuando el history
    reporta un error de ejecución cuyo texto contiene 'CUDA'/'out of memory'/
    'OutOfMemory'. NO transitoria (reintentar vuelve a saturar los 4 GB de VRAM).
    """

    fallback_reason = "cuda_oom"
    transient = False


class ComfyUIImageError(ComfyUIError):
    """La imagen descargada está vacía o corrupta (no decodificable por PIL).

    Se detecta en :meth:`ComfyUIImageProvider._download` cuando ``/view`` devuelve
    bytes vacíos o no validables con ``PIL.Image.open().verify()``. NO transitoria.
    """

    fallback_reason = "corrupt_image"
    transient = False


# --------------------------------------------------------------------------- #
# Capacidades del servidor ComfyUI (detección por versión)
# --------------------------------------------------------------------------- #


@dataclass
class ComfyUICapabilities:
    """Capacidades detectadas del servidor ComfyUI (punto de detección por versión).

    En FEAT-002 solo se define la estructura y un constructor por defecto
    (:func:`default_capabilities`) con todo asumido disponible. La detección real
    (consultar ``/system_stats`` y la versión por red) se implementa en FEAT-003/005;
    ahí se poblarán estos flags en función de lo que el servidor exponga.
    """

    supports_system_stats: bool = True
    supports_queue: bool = True
    supports_free: bool = True
    version: str | None = None


def default_capabilities(version: str | None = None) -> ComfyUICapabilities:
    """Capacidades por defecto (todo disponible) antes de cualquier detección por red."""
    return ComfyUICapabilities(
        supports_system_stats=True,
        supports_queue=True,
        supports_free=True,
        version=version,
    )


def _extract_comfyui_version(data: Any) -> str | None:
    """Extrae ``system.comfyui_version`` del payload de ``/system_stats`` si existe."""
    if not isinstance(data, dict):
        return None
    system = data.get("system")
    if isinstance(system, dict):
        version = system.get("comfyui_version") or system.get("version")
        if version is not None:
            return str(version)
    return None


def _has_loaded_models(stats: dict[str, Any]) -> bool:
    """¿El payload de ``/system_stats`` indica modelos/VRAM en uso por ComfyUI?

    Best-effort (refinamiento 1): ComfyUI no expone un campo canónico y estable de
    «modelos cargados» en todas las versiones, así que se interpretan las pistas
    habituales: una lista ``loaded_models`` no vacía, o un dispositivo con
    ``vram_free < vram_total`` (hay algo ocupando VRAM). Ante la duda (sin campos
    reconocibles) se devuelve ``True`` para no omitir una liberación necesaria.
    """
    loaded = stats.get("loaded_models")
    if isinstance(loaded, list):
        return len(loaded) > 0

    devices = stats.get("devices")
    if isinstance(devices, list) and devices:
        saw_vram = False
        for device in devices:
            if not isinstance(device, dict):
                continue
            total = device.get("vram_total")
            free = device.get("vram_free")
            if isinstance(total, (int, float)) and isinstance(free, (int, float)):
                saw_vram = True
                if free < total:
                    return True
        if saw_vram:
            # Se pudo leer la VRAM y estaba toda libre -> nada cargado.
            return False

    # Sin señales reconocibles: conservador (asumir que podría haber algo cargado).
    return True


# --------------------------------------------------------------------------- #
# Concurrencia (punto 1): semáforo a nivel de módulo
# --------------------------------------------------------------------------- #
#
# DECISIÓN (ver docs/MOTOR-COMFYUI.md): el worker actual ya es secuencial (la cola
# de jobs toma un job a la vez vía ``JobQueue.claim``), así que en el camino normal
# nunca hay dos generaciones SD a la vez. El semáforo a NIVEL DE MÓDULO NO duplica la
# cola de jobs: protege el invariante «nunca dos generaciones SD simultáneas» aunque
# en el futuro haya varios workers/hilos llamando a ``image_for`` en el mismo proceso.
# El límite lo fija ``AIV_SD_MAX_CONCURRENCY`` (default 1); es reconfigurable e
# inyectable en test vía :func:`_get_sd_semaphore` / :func:`_reset_sd_semaphore`.

_SD_SEMAPHORE: threading.BoundedSemaphore | None = None
_SD_SEMAPHORE_VALUE: int | None = None
_SD_SEMAPHORE_LOCK = threading.Lock()


def _get_sd_semaphore() -> threading.BoundedSemaphore:
    """Devuelve el semáforo (perezoso) que serializa las generaciones SD.

    Se construye con ``get_settings().sd_max_concurrency`` la primera vez. Si el valor
    de configuración cambia (p. ej. en un test con ``monkeypatch`` del setting) el
    semáforo se reconstruye para reflejar el nuevo límite. Mínimo efectivo = 1.
    """
    global _SD_SEMAPHORE, _SD_SEMAPHORE_VALUE
    desired = max(int(get_settings().sd_max_concurrency), 1)
    with _SD_SEMAPHORE_LOCK:
        if _SD_SEMAPHORE is None or _SD_SEMAPHORE_VALUE != desired:
            _SD_SEMAPHORE = threading.BoundedSemaphore(value=desired)
            _SD_SEMAPHORE_VALUE = desired
        return _SD_SEMAPHORE


def _reset_sd_semaphore() -> None:
    """Descarta el semáforo cacheado (para tests que cambian el límite)."""
    global _SD_SEMAPHORE, _SD_SEMAPHORE_VALUE
    with _SD_SEMAPHORE_LOCK:
        _SD_SEMAPHORE = None
        _SD_SEMAPHORE_VALUE = None


# Caché de capacidades detectadas por URL (una sola detección por servidor).
_CAPABILITIES_CACHE: dict[str, ComfyUICapabilities] = {}
_CAPABILITIES_LOCK = threading.Lock()


def _reset_capabilities_cache() -> None:
    """Vacía la caché de capacidades detectadas (para tests)."""
    with _CAPABILITIES_LOCK:
        _CAPABILITIES_CACHE.clear()


# --------------------------------------------------------------------------- #
# Lógica pura: dimensiones de la latente
# --------------------------------------------------------------------------- #


def base_size_for_aspect(aspect: str, base_size: int) -> tuple[int, int]:
    """Dimensiones ``(width, height)`` de la latente de generación para ``aspect``.

    Reutiliza :func:`app.providers.image.aspect.aspect_to_dims` para obtener la
    proporción del aspecto y la reescala de modo que el **lado largo** quede en
    ``base_size``. El resultado se redondea a múltiplos de 8 (requisito del VAE de
    Stable Diffusion) con un mínimo de 8 por lado.

    La latente es pequeña a propósito (4 GB de VRAM); el tamaño final de entrega se
    alcanza con un upscale posterior, fuera de esta función.
    """
    ref_w, ref_h = aspect_to_dims(aspect)
    long_ref = max(ref_w, ref_h) or 1
    scale = base_size / long_ref
    width = _round_to_multiple_of_8(ref_w * scale)
    height = _round_to_multiple_of_8(ref_h * scale)
    return (width, height)


def _round_to_multiple_of_8(value: float) -> int:
    """Redondea ``value`` al múltiplo de 8 más cercano, con mínimo 8."""
    rounded = int(round(value / 8.0)) * 8
    return max(rounded, 8)


# --------------------------------------------------------------------------- #
# Lógica pura: construcción del grafo del workflow
# --------------------------------------------------------------------------- #


def build_workflow(
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
    ckpt_name: str,
    template_path: str | Path | None = None,
    *,
    style_refs: list[str] | None = None,
    char_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Construye el grafo del workflow en formato *API* de ComfyUI.

    Si ``template_path`` apunta a un JSON existente, lo carga y sustituye los
    marcadores ``{{prompt}}``, ``{{negative}}``, ``{{width}}``, ``{{height}}``,
    ``{{seed}}``, ``{{steps}}``, ``{{cfg}}``, ``{{sampler}}``, ``{{scheduler}}`` y
    ``{{ckpt_name}}`` por los valores dados. En caso contrario devuelve el grafo
    SD 1.5 embebido con los nodos ``CheckpointLoaderSimple``, ``CLIPTextEncode``
    (positivo y negativo), ``EmptyLatentImage``, ``KSampler``, ``VAEDecode`` y
    ``SaveImage`` conectados.

    ``style_refs`` / ``char_refs`` son el **punto de extensión** para la consistencia
    visual (punto 5 de la revisión): referencias de estilo y de personaje que en el
    futuro alimentarán nodos tipo IPAdapter / ControlNet / LoRA. Por ahora son
    PLACEHOLDERS DE DISEÑO: se aceptan pero se ignoran (default ``None``) y NO alteran
    el grafo mientras valgan ``None`` (el hash del grafo no cambia por pasarlos vacíos).
    """
    if template_path:
        path = Path(template_path)
        if path.is_file():
            return _build_from_template(
                path,
                prompt=prompt,
                negative=negative,
                width=width,
                height=height,
                seed=seed,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                scheduler=scheduler,
                ckpt_name=ckpt_name,
            )

    return _build_embedded(
        prompt=prompt,
        negative=negative,
        width=width,
        height=height,
        seed=seed,
        steps=steps,
        cfg=cfg,
        sampler=sampler,
        scheduler=scheduler,
        ckpt_name=ckpt_name,
    )


def _build_embedded(
    *,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
    ckpt_name: str,
) -> dict[str, Any]:
    """Grafo SD 1.5 estándar (text-to-image) en formato API de ComfyUI.

    Las claves "1".."7" son los ids de nodo; los enlaces se expresan como
    ``[id_nodo, indice_salida]`` tal y como espera el endpoint ``/prompt``.
    """
    return {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["4", 1]},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler,
                "scheduler": scheduler,
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "aiv_sd", "images": ["8", 0]},
        },
    }


def _build_from_template(
    path: Path,
    *,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
    ckpt_name: str,
) -> dict[str, Any]:
    """Carga un workflow JSON externo y sustituye sus marcadores ``{{...}}``.

    La sustitución es textual sobre el JSON serializado, de modo que los marcadores
    pueden aparecer tanto en valores string (``"{{prompt}}"``) como embebidos dentro
    de una cadena. Los valores numéricos se inyectan sin comillas para que el JSON
    resultante conserve su tipo.
    """
    raw = path.read_text(encoding="utf-8")
    replacements = {
        "prompt": _json_fragment(prompt),
        "negative": _json_fragment(negative),
        "width": _json_fragment(width),
        "height": _json_fragment(height),
        "seed": _json_fragment(seed),
        "steps": _json_fragment(steps),
        "cfg": _json_fragment(cfg),
        "sampler": _json_fragment(sampler),
        "scheduler": _json_fragment(scheduler),
        "ckpt_name": _json_fragment(ckpt_name),
    }
    for key in _TEMPLATE_PLACEHOLDERS:
        token = "{{" + key + "}}"
        raw = raw.replace('"' + token + '"', replacements[key])
        raw = raw.replace(token, _inline_fragment(replacements[key]))
    return json.loads(raw)


def _json_fragment(value: Any) -> str:
    """Serializa ``value`` como fragmento JSON (string entrecomillado o número)."""
    return json.dumps(value, ensure_ascii=False)


def _inline_fragment(fragment: str) -> str:
    """Devuelve el fragmento JSON sin las comillas externas (sustitución embebida)."""
    if len(fragment) >= 2 and fragment[0] == '"' and fragment[-1] == '"':
        return fragment[1:-1]
    return fragment


# --------------------------------------------------------------------------- #
# Lógica pura: versionado y hash del workflow
# --------------------------------------------------------------------------- #


def workflow_version(template_path: str | Path | None) -> str:
    """Identificador de versión del workflow.

    Devuelve ``'sd15-embedded-v1'`` para el grafo embebido o ``'custom:<stem>'``
    (nombre del fichero de plantilla sin extensión) cuando se usa una plantilla.
    """
    if template_path:
        stem = Path(template_path).stem
        return f"custom:{stem}"
    return EMBEDDED_WORKFLOW_VERSION


def workflow_hash(graph: dict) -> str:
    """Hash determinista del grafo final del workflow.

    Serializa el grafo de forma canónica (``sort_keys=True``, sin espacios), de modo
    que el hash es INVARIANTE al orden de inserción de claves pero SENSIBLE a cualquier
    cambio de parámetro (seed, steps, cfg, sampler, prompt, checkpoint, tamaño...).
    Devuelve ``'sha256:'`` seguido de los 64 dígitos hex del digest.
    """
    canonical = json.dumps(
        graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# --------------------------------------------------------------------------- #
# Lógica pura: shape del meta de generación
# --------------------------------------------------------------------------- #

# Claves del shape canónico del meta de generación (punto 4), en orden estable.
_GENERATION_META_KEYS = (
    "provider",
    "model",
    "prompt",
    "negative_prompt",
    "seed",
    "width",
    "height",
    "steps",
    "sampler",
    "cfg",
    "generation_time_s",
    "status",
    "fallback_reason",
    "workflow_version",
    "workflow_hash",
)


def build_generation_meta(
    *,
    provider: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
    negative_prompt: str | None = None,
    seed: int | None = None,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    sampler: str | None = None,
    cfg: float | None = None,
    generation_time_s: float | None = None,
    status: str | None = None,
    fallback_reason: str | None = None,
    workflow_version: str | None = None,
    workflow_hash: str | None = None,
    graph: dict | None = None,
) -> dict[str, Any]:
    """Devuelve el *shape* canónico del meta de generación (punto 4).

    El dict resultante SIEMPRE contiene las claves de :data:`_GENERATION_META_KEYS`;
    las que no se provean quedan en ``None`` (no se inventan valores). ``status`` se
    espera en ``{'ok', 'fallback'}``.

    ``workflow_version`` y ``workflow_hash`` identifican el grafo que produjo la
    imagen. Si no se pasan explícitamente pero sí ``graph``, se derivan de él
    (``workflow_hash`` vía :func:`workflow_hash`). Esto permite poblar el meta tanto
    con los valores ya calculados como a partir del propio grafo.
    """
    if workflow_hash is None and graph is not None:
        workflow_hash = _hash_graph(graph)

    return {
        "provider": provider,
        "model": model,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "seed": seed,
        "width": width,
        "height": height,
        "steps": steps,
        "sampler": sampler,
        "cfg": cfg,
        "generation_time_s": generation_time_s,
        "status": status,
        "fallback_reason": fallback_reason,
        "workflow_version": workflow_version,
        "workflow_hash": workflow_hash,
    }


def _hash_graph(graph: dict) -> str:
    """Alias interno de :func:`workflow_hash` (evita sombra del parámetro homónimo)."""
    return workflow_hash(graph)


# --------------------------------------------------------------------------- #
# Provider (esqueleto; la parte de red llega en FEAT-003)
# --------------------------------------------------------------------------- #


class ComfyUIImageProvider:
    """Implementa ``ImageProvider`` generando imágenes con ComfyUI local.

    Lee su configuración de ``Settings`` (patrón de ``StockImageProvider``) y mantiene
    un ``PlaceholderProvider`` como red de seguridad. :meth:`image_for` encola el
    workflow en ``/prompt``, espera en ``/history``, descarga en ``/view`` y libera
    VRAM en ``/free``; valida el PNG con Pillow y, ante cualquier :class:`ComfyUIError`
    (tras agotar reintentos de las categorías transitorias), degrada honestamente al
    fallback conservando ``provider='placeholder'`` con ``meta.status='fallback'`` y
    ``meta.fallback_reason`` = la categoría del fallo.
    """

    name = "sd_local"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        checkpoint: str | None = None,
        steps: int | None = None,
        sampler: str | None = None,
        scheduler: str | None = None,
        cfg: float | None = None,
        base_size: int | None = None,
        negative_prompt: str | None = None,
        workflow_path: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (settings.sd_local_url if base_url is None else base_url).rstrip("/")
        self.checkpoint = settings.sd_checkpoint if checkpoint is None else checkpoint
        self.steps = settings.sd_steps if steps is None else steps
        self.sampler = settings.sd_sampler if sampler is None else sampler
        self.scheduler = settings.sd_scheduler if scheduler is None else scheduler
        self.cfg = settings.sd_cfg if cfg is None else cfg
        self.base_size = settings.sd_base_size if base_size is None else base_size
        self.negative_prompt = (
            settings.sd_negative_prompt if negative_prompt is None else negative_prompt
        )
        self.workflow_path = (
            settings.sd_workflow_path if workflow_path is None else workflow_path
        )
        self.enabled = settings.sd_local_enabled if enabled is None else enabled
        self.negative = self.negative_prompt
        self.timeout_s = settings.sd_timeout_s
        self.poll_interval_s = settings.sd_poll_interval_s
        self.connect_timeout_s = settings.sd_connect_timeout_s
        self.max_retries = settings.sd_max_retries
        self.free_before = settings.sd_free_before
        self.free_after = settings.sd_free_after
        # Red de seguridad: si ComfyUI no está disponible, se degrada a placeholder.
        self._fallback = PlaceholderProvider()

    def image_for(
        self,
        query: str,
        *,
        aspect: str,
        out_dir: str,
        seed: int | None = None,
    ) -> ImageAsset:
        """Genera la imagen de ``query`` con ComfyUI, o degrada a placeholder.

        Flujo: ``_submit`` (POST /prompt) -> ``_poll_history`` (GET /history/<id>) ->
        ``_download`` (GET /view) + validación PIL. Las categorías transitorias
        (connection/timeout) se reintentan hasta ``sd_max_retries`` con backoff; las
        no transitorias (checkpoint_missing/invalid_workflow/cuda_oom/corrupt_image)
        NO se reintentan. ``image_for`` NUNCA lanza: ante cualquier
        :class:`ComfyUIError` degrada al fallback, y el ``ImageAsset`` resultante es
        del provider ``placeholder`` (nunca ``sd_local``) con ``meta.status='fallback'``
        y ``meta.fallback_reason`` = la categoría, para no presentar como real algo
        que no lo es.
        """
        if not self.enabled or not self.base_url:
            logger.info(
                "ComfyUI deshabilitado o sin URL (enabled=%s, url=%r); degradando a "
                "placeholder.",
                self.enabled,
                self.base_url,
            )
            return self._degrade(query, aspect, out_dir, seed, reason="disabled")

        # CONCURRENCIA (punto 1): se adquiere el semáforo ANTES de cualquier trabajo de
        # red y se libera SIEMPRE en el finally (éxito, error y degradación), de modo
        # que un fallo nunca deja el lock tomado y bloquea generaciones posteriores.
        semaphore = _get_sd_semaphore()
        semaphore.acquire()
        try:
            return self._image_for_locked(query, aspect, out_dir, seed)
        finally:
            semaphore.release()

    def _image_for_locked(
        self,
        query: str,
        aspect: str,
        out_dir: str,
        seed: int | None,
    ) -> ImageAsset:
        """Cuerpo de :meth:`image_for` ejecutado bajo el semáforo de concurrencia."""
        seed_value = 0 if seed is None else int(seed)
        t0 = time.monotonic()
        width, height = base_size_for_aspect(aspect, self.base_size)
        graph = build_workflow(
            prompt=query,
            negative=self.negative,
            width=width,
            height=height,
            seed=seed_value,
            steps=self.steps,
            cfg=self.cfg,
            sampler=self.sampler,
            scheduler=self.scheduler,
            ckpt_name=self.checkpoint,
            template_path=self.workflow_path or None,
        )

        try:
            file_path = self._generate_with_retries(graph, out_dir)
        except ComfyUIError as exc:
            logger.warning(
                "Generación ComfyUI fallida (%s) contra %s; degradando a placeholder.",
                exc.fallback_reason,
                self.base_url,
            )
            return self._degrade(
                query, aspect, out_dir, seed, reason=exc.fallback_reason
            )

        meta = build_generation_meta(
            provider=self.name,
            model=self.checkpoint,
            prompt=query,
            negative_prompt=self.negative,
            seed=seed_value,
            width=width,
            height=height,
            steps=self.steps,
            sampler=self.sampler,
            cfg=self.cfg,
            generation_time_s=max(time.monotonic() - t0, 0.0),
            status="ok",
            fallback_reason=None,
            workflow_version=workflow_version(self.workflow_path or None),
            graph=graph,
        )
        meta["scheduler"] = self.scheduler
        meta["backend_engine"] = "comfyui"
        # Resolución REAL de generación SD bajo claves propias del provider. VisualEngine
        # sobreescribe meta['width']/meta['height'] con las del ImageAsset (dimensiones
        # del aspecto objetivo); gen_width/gen_height conservan la latente generada sin
        # colisionar con esos valores, de modo que la trazabilidad sobrevive.
        meta["gen_width"] = width
        meta["gen_height"] = height
        return ImageAsset(
            path=str(file_path),
            width=width,
            height=height,
            provider=self.name,
            query=query,
            seed=seed_value,
            meta=meta,
        )

    # --- orquestación con reintentos ---------------------------------------- #

    def _generate_with_retries(self, graph: dict[str, Any], out_dir: str) -> Path:
        """Ejecuta submit->poll->download reintentando SOLO categorías transitorias.

        Reintenta hasta ``sd_max_retries`` veces adicionales (backoff lineal sobre
        ``sd_poll_interval_s``) cuando la :class:`ComfyUIError` es transitoria
        (connection/timeout). Las no transitorias se propagan al primer intento.
        Libera VRAM antes/después según ``sd_free_before``/``sd_free_after``.
        """
        attempts = max(self.max_retries, 0) + 1
        last_exc: ComfyUIError | None = None
        for attempt in range(attempts):
            try:
                if self.free_before:
                    self._free_vram(reason="free_before")
                prompt_id = self._submit(graph)
                images = self._poll_history(prompt_id)
                file_path = self._download(images[0], out_dir)
                if self.free_after:
                    self._free_vram(reason="free_after")
                return file_path
            except ComfyUIError as exc:
                last_exc = exc
                if self.free_after:
                    self._free_vram(reason="free_after_error")
                if not exc.transient or attempt >= attempts - 1:
                    raise
                backoff = self.poll_interval_s * (attempt + 1)
                logger.info(
                    "Reintento %d/%d tras error transitorio '%s'; backoff %.2fs.",
                    attempt + 1,
                    attempts - 1,
                    exc.fallback_reason,
                    backoff,
                )
                time.sleep(backoff)
        # Inalcanzable salvo attempts<=0; por seguridad re-lanza el último error.
        raise last_exc if last_exc is not None else ComfyUIError()

    # --- parte de red (httpx a self.base_url) ------------------------------- #

    def _submit(self, graph: dict[str, Any]) -> str:
        """Encola el grafo en ``POST {url}/prompt`` y devuelve el ``prompt_id``.

        Clasifica los errores de validación de la respuesta: un mensaje que indique
        checkpoint/ckpt no encontrado -> :class:`ComfyUICheckpointError`; otros
        ``node_errors``/``error`` de validación -> :class:`ComfyUIWorkflowError`.
        ``httpx.ConnectError``/``ConnectTimeout`` -> :class:`ComfyUIConnectionError`;
        ``httpx.TimeoutException`` -> :class:`ComfyUITimeoutError`.
        """
        body = {"prompt": graph, "client_id": uuid.uuid4().hex}
        try:
            resp = httpx.post(
                f"{self.base_url}/prompt",
                json=body,
                timeout=self.connect_timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ComfyUIConnectionError(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise ComfyUITimeoutError(str(exc)) from exc
        except httpx.HTTPError as exc:
            # Un 4xx/5xx de validación de ComfyUI: tratar como workflow inválido.
            raise ComfyUIWorkflowError(str(exc)) from exc

        if isinstance(data, dict) and (data.get("error") or data.get("node_errors")):
            text = json.dumps(data, ensure_ascii=False).lower()
            if "ckpt" in text or "checkpoint" in text:
                raise ComfyUICheckpointError(json.dumps(data, ensure_ascii=False))
            raise ComfyUIWorkflowError(json.dumps(data, ensure_ascii=False))

        prompt_id = data.get("prompt_id") if isinstance(data, dict) else None
        if not prompt_id:
            raise ComfyUIWorkflowError(f"Respuesta de /prompt sin prompt_id: {data!r}")
        return str(prompt_id)

    def _poll_history(self, prompt_id: str) -> list[dict[str, Any]]:
        """Sondea ``GET {url}/history/{prompt_id}`` hasta obtener imágenes.

        Devuelve la lista de referencias de imagen (dicts con filename/subfolder/
        type) del primer nodo con salida ``images``. Supera ``sd_timeout_s`` ->
        :class:`ComfyUITimeoutError`. Error de ejecución de nodo con texto
        'CUDA'/'out of memory'/'OutOfMemory' -> :class:`ComfyUICudaError`.
        """
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                resp = httpx.get(
                    f"{self.base_url}/history/{prompt_id}",
                    timeout=self.connect_timeout_s,
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise ComfyUIConnectionError(str(exc)) from exc
            except httpx.TimeoutException as exc:
                raise ComfyUITimeoutError(str(exc)) from exc
            except httpx.HTTPError as exc:
                raise ComfyUIConnectionError(str(exc)) from exc

            entry = data.get(prompt_id) if isinstance(data, dict) else None
            if entry:
                self._raise_if_execution_error(entry)
                images = self._extract_images(entry)
                if images:
                    return images

            if time.monotonic() >= deadline:
                raise ComfyUITimeoutError(
                    f"Sin resultado para {prompt_id} tras {self.timeout_s}s."
                )
            time.sleep(self.poll_interval_s)

    @staticmethod
    def _raise_if_execution_error(entry: dict[str, Any]) -> None:
        """Clasifica errores de ejecución reportados en el history del prompt."""
        status = entry.get("status") if isinstance(entry, dict) else None
        texts: list[str] = []
        if isinstance(status, dict):
            if status.get("status_str") == "error":
                texts.append(json.dumps(status, ensure_ascii=False))
            for item in status.get("messages") or []:
                texts.append(json.dumps(item, ensure_ascii=False))
        blob = " ".join(texts).lower()
        if not blob:
            return
        if "cuda" in blob or "out of memory" in blob or "outofmemory" in blob:
            raise ComfyUICudaError(blob)
        if "error" in blob:
            raise ComfyUIWorkflowError(blob)

    @staticmethod
    def _extract_images(entry: dict[str, Any]) -> list[dict[str, Any]]:
        """Extrae las referencias de imagen de los ``outputs`` del history."""
        outputs = entry.get("outputs") if isinstance(entry, dict) else None
        if not isinstance(outputs, dict):
            return []
        for node_output in outputs.values():
            images = node_output.get("images") if isinstance(node_output, dict) else None
            if images:
                return list(images)
        return []

    def _download(self, image_ref: dict[str, Any], out_dir: str) -> Path:
        """Descarga ``GET {url}/view`` y valida el PNG con Pillow antes de escribir.

        Bytes vacíos o no decodificables por ``PIL.Image.open().verify()`` ->
        :class:`ComfyUIImageError`. Solo se escribe a disco si la imagen valida.
        """
        params = {
            "filename": image_ref.get("filename"),
            "subfolder": image_ref.get("subfolder", ""),
            "type": image_ref.get("type", "output"),
        }
        try:
            resp = httpx.get(
                f"{self.base_url}/view",
                params=params,
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            content = resp.content
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ComfyUIConnectionError(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise ComfyUITimeoutError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ComfyUIConnectionError(str(exc)) from exc

        if not content:
            raise ComfyUIImageError("Descarga vacía desde /view.")
        try:
            Image.open(io.BytesIO(content)).verify()
        except Exception as exc:  # PIL lanza varios tipos; cualquiera = corrupta.
            raise ComfyUIImageError(f"Imagen no decodificable: {exc}") from exc

        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        filename = image_ref.get("filename") or "comfyui_output.png"
        stem = hashlib.sha256(
            f"{params['subfolder']}:{filename}".encode()
        ).hexdigest()[:16]
        file_path = out_path / f"sd_local_{stem}.png"
        file_path.write_bytes(content)
        return file_path

    # --- detección de capacidades / versión (refinamiento 4, punto D) -------- #

    def capabilities(self) -> ComfyUICapabilities:
        """Capacidades del servidor (perezosas, cacheadas por URL).

        Consulta ``GET {url}/system_stats`` la primera vez para este ``base_url`` y
        cachea el resultado: qué endpoints soporta (system_stats/queue/free) y la
        versión (``system.comfyui_version`` si aparece). La detección es tolerante: un
        404/405 o un ``ConnectError`` marca ``supports_system_stats=False`` (sin asumir
        que el resto falte; /queue y /free se prueban en el momento de usarlos y cada
        404 desactiva su flag). El provider NUNCA rompe por falta de un endpoint.
        """
        with _CAPABILITIES_LOCK:
            cached = _CAPABILITIES_CACHE.get(self.base_url)
            if cached is not None:
                return cached
        caps = self._detect_capabilities()
        with _CAPABILITIES_LOCK:
            _CAPABILITIES_CACHE.setdefault(self.base_url, caps)
            return _CAPABILITIES_CACHE[self.base_url]

    def _detect_capabilities(self) -> ComfyUICapabilities:
        """Detecta capacidades consultando ``GET {url}/system_stats`` (tolerante)."""
        caps = default_capabilities()
        try:
            resp = httpx.get(
                f"{self.base_url}/system_stats", timeout=self.connect_timeout_s
            )
            status = getattr(resp, "status_code", None)
            if status in (404, 405):
                caps.supports_system_stats = False
                logger.info(
                    "ComfyUI %s: /system_stats no soportado (HTTP %s); se omite la "
                    "verificación de estado previo.",
                    self.base_url,
                    status,
                )
                return caps
            resp.raise_for_status()
            data = resp.json()
            caps.supports_system_stats = True
            caps.version = _extract_comfyui_version(data)
            logger.info(
                "ComfyUI %s: versión=%s, capacidades system_stats/queue/free "
                "(detección por versión).",
                self.base_url,
                caps.version,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as exc:
            caps.supports_system_stats = False
            logger.info(
                "ComfyUI %s: no se pudo detectar capacidades (%s); se continúa de "
                "forma conservadora.",
                self.base_url,
                exc,
            )
        except httpx.HTTPError as exc:
            caps.supports_system_stats = False
            logger.info(
                "ComfyUI %s: /system_stats devolvió error (%s); se omite la "
                "verificación de estado previo.",
                self.base_url,
                exc,
            )
        return caps

    def _system_stats(self) -> dict[str, Any] | None:
        """Consulta ``GET {url}/system_stats`` devolviendo el dict o ``None`` (tolerante)."""
        try:
            resp = httpx.get(
                f"{self.base_url}/system_stats", timeout=self.connect_timeout_s
            )
            if getattr(resp, "status_code", None) in (404, 405):
                return None
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else None
        except httpx.HTTPError as exc:
            logger.info("ComfyUI %s: /system_stats no disponible (%s).", self.base_url, exc)
            return None

    # --- guarda de cola (refinamiento 2, punto B) ---------------------------- #

    def _comfyui_busy(self) -> bool:
        """¿ComfyUI tiene trabajos en curso o en cola? (``GET {url}/queue``).

        Devuelve ``True`` si ``queue_running`` o ``queue_pending`` traen elementos.
        TOLERANTE y CONSERVADOR: si ``/queue`` no existe/falla no se puede confirmar
        que el servidor esté libre, así que se devuelve ``True`` (no liberar) para no
        romper generaciones de otros procesos/usuarios. Lo registra.
        """
        try:
            resp = httpx.get(f"{self.base_url}/queue", timeout=self.connect_timeout_s)
            if getattr(resp, "status_code", None) in (404, 405):
                logger.info(
                    "ComfyUI %s: /queue no soportado; se asume ocupado (conservador).",
                    self.base_url,
                )
                return True
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "ComfyUI %s: no se pudo consultar /queue (%s); se asume ocupado "
                "(conservador) y no se libera VRAM.",
                self.base_url,
                exc,
            )
            return True

        if not isinstance(data, dict):
            return True
        running = data.get("queue_running") or []
        pending = data.get("queue_pending") or []
        busy = bool(running) or bool(pending)
        if busy:
            logger.info(
                "ComfyUI %s: cola ocupada (running=%d, pending=%d).",
                self.base_url,
                len(running),
                len(pending),
            )
        return busy

    # --- liberación de VRAM con guardas (refinamientos 1-4) ------------------ #

    def _free_vram(self, reason: str = "manual", meta: dict | None = None) -> None:
        """Libera VRAM con ``POST {url}/free`` aplicando las cuatro guardas en orden.

        Orden (nunca lanza; cualquier fallo se registra SIN ocultarlo y se continúa):

        1. Si ``not capabilities.supports_free`` -> OMITIR con ``log.info`` (compat por
           versión, refinamiento 4).
        2. Verificar estado previo vía ``/system_stats``: si el servidor reporta que no
           hay nada cargado atribuible a ComfyUI, no se fuerza ``/free`` (refinamiento 1,
           best-effort con log).
        3. Si :meth:`_comfyui_busy` -> OMITIR con ``log.warning`` explicando que hay
           trabajos en curso/cola (refinamiento 2).
        4. En otro caso, ``POST /free``. Un 500/ConnectError/timeout/404 se registra con
           ``log.warning``/``log.error`` y el detalle (refinamiento 3) y se refleja
           opcionalmente en ``meta['free_vram_error']``.
        """
        caps = self.capabilities()

        # (1) compatibilidad por versión: el servidor no expone /free.
        if not caps.supports_free:
            logger.info(
                "ComfyUI %s: /free no soportado por esta versión; se omite la "
                "liberación de VRAM (%s).",
                self.base_url,
                reason,
            )
            return

        # (2) estado previo: no liberar lo que no está cargado (best-effort).
        if caps.supports_system_stats:
            stats = self._system_stats()
            if stats is not None and not _has_loaded_models(stats):
                logger.info(
                    "ComfyUI %s: /system_stats indica que no hay modelos cargados; no "
                    "se fuerza /free (%s).",
                    self.base_url,
                    reason,
                )
                return

        # (3) guarda de cola: no liberar si hay trabajos en curso/cola.
        if self._comfyui_busy():
            logger.warning(
                "ComfyUI %s: hay trabajos en curso o en cola; NO se libera VRAM para "
                "no interrumpir otras generaciones (%s).",
                self.base_url,
                reason,
            )
            return

        # (4) liberar.
        try:
            resp = httpx.post(
                f"{self.base_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=self.connect_timeout_s,
            )
            if getattr(resp, "status_code", None) in (404, 405):
                caps.supports_free = False
                logger.warning(
                    "ComfyUI %s: /free devolvió HTTP %s; se marca como no soportado y "
                    "se omite en adelante (%s).",
                    self.base_url,
                    resp.status_code,
                    reason,
                )
                if meta is not None:
                    meta["free_vram_error"] = f"http_{resp.status_code}"
                return
            resp.raise_for_status()
            logger.info("ComfyUI %s: VRAM liberada vía /free (%s).", self.base_url, reason)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as exc:
            logger.warning(
                "ComfyUI %s: fallo al liberar VRAM vía /free (%s): %s; se continúa sin "
                "liberar.",
                self.base_url,
                reason,
                exc,
            )
            if meta is not None:
                meta["free_vram_error"] = str(exc)
        except httpx.HTTPError as exc:
            logger.error(
                "ComfyUI %s: /free devolvió error (%s): %s; se continúa sin liberar.",
                self.base_url,
                reason,
                exc,
            )
            if meta is not None:
                meta["free_vram_error"] = str(exc)

    # --- degradación honesta ------------------------------------------------- #

    def _degrade(
        self,
        query: str,
        aspect: str,
        out_dir: str,
        seed: int | None,
        *,
        reason: str,
    ) -> ImageAsset:
        """Degrada al ``PlaceholderProvider`` conservando trazabilidad del fallo.

        El Asset resultante es del provider ``placeholder`` (NUNCA ``sd_local``) y su
        ``meta`` incorpora ``status='fallback'`` y ``fallback_reason=reason`` sobre el
        meta del placeholder (que se preserva para atribución/licencia).
        """
        asset = self._fallback.image_for(
            query, aspect=aspect, out_dir=out_dir, seed=seed
        )
        meta = dict(asset.meta or {})
        meta["status"] = "fallback"
        meta["fallback_reason"] = reason
        meta["requested_provider"] = self.name
        asset.meta = meta
        return asset
