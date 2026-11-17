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
2. **Provider (parte de red):** :class:`ComfyUIImageProvider`, que en FEAT-002 es un
   esqueleto con la firma del ``Protocol`` y degradación a ``PlaceholderProvider``.
   La conversación HTTP real (encolar/esperar/descargar/liberar VRAM) llega en FEAT-003.

``httpx`` se importa a nivel de módulo (como en ``stock.py``) para que los tests de
red de FEAT-003 puedan mockear ``app.providers.image.comfyui.httpx``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.providers.base import ImageAsset
from app.providers.image.aspect import aspect_to_dims
from app.providers.image.placeholder import PlaceholderProvider

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

    En FEAT-002 es un ESQUELETO: lee su configuración de ``Settings`` (patrón de
    ``StockImageProvider``) y mantiene un ``PlaceholderProvider`` como red de
    seguridad. :meth:`image_for` todavía no habla con ComfyUI; la conversación HTTP
    (encolar en ``/prompt``, esperar en ``/history``, descargar y liberar VRAM con
    ``/free``) se implementa en FEAT-003 en el lugar marcado con ``TODO``.
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

        En FEAT-002 esto es un esqueleto: aún no hay red, así que delega en el
        fallback. En degradación el ``ImageAsset`` resultante es del provider
        ``placeholder`` (nunca ``sd_local``) para no presentar como real algo que no
        lo es.
        """
        # TODO(FEAT-003): parte de red con ComfyUI vía httpx:
        #   1. (opcional) POST /free si self.free_before.
        #   2. base_size_for_aspect(aspect, self.base_size) -> (width, height).
        #   3. build_workflow(...) con los parámetros del provider y el seed.
        #   4. POST /prompt para encolar; sondear GET /history/<id> hasta terminar
        #      (self.poll_interval_s / self.timeout_s), con self.max_retries.
        #   5. Descargar la imagen vía GET /view y escribirla en out_dir.
        #   6. (opcional) POST /free si self.free_after.
        #   7. Construir el ImageAsset con build_generation_meta(status='ok', graph=...).
        # Ante cualquier fallo de red/servidor: degradar al fallback (status='fallback').
        return self._fallback.image_for(query, aspect=aspect, out_dir=out_dir, seed=seed)
