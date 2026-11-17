"""Lógica pura del motor ComfyUI (FEAT-002): workflow, dimensiones, meta, hash.

Sin red ni ComfyUI reales (patrón de los tests de lógica pura): se verifican la
construcción del grafo del workflow, el mapeo aspecto→dimensiones de la latente, el
*shape* canónico del meta de generación, el versionado y el hash determinista del
grafo, y que ``get_settings()`` expone los nuevos ``AIV_SD_*`` / ``AIV_OLLAMA_*`` sin
necesidad de ``.env``.
"""

from __future__ import annotations

import json

from app.config import Settings
from app.providers.image.comfyui import (
    ComfyUICapabilities,
    build_generation_meta,
    build_workflow,
    base_size_for_aspect,
    default_capabilities,
    workflow_hash,
    workflow_version,
)

# Parámetros de referencia compartidos por los tests.
_BASE = dict(
    prompt="un gato astronauta",
    negative="lowres, blurry",
    width=640,
    height=360,
    seed=1234,
    steps=20,
    cfg=7.0,
    sampler="euler",
    scheduler="normal",
    ckpt_name="v1-5-pruned-emaonly.safetensors",
)


# --------------------------------------------------------------------------- #
# build_workflow (grafo embebido)
# --------------------------------------------------------------------------- #


def test_build_workflow_tiene_los_nodos_sd15():
    graph = build_workflow(**_BASE)
    class_types = {node["class_type"] for node in graph.values()}
    assert {
        "CheckpointLoaderSimple",
        "CLIPTextEncode",
        "EmptyLatentImage",
        "KSampler",
        "VAEDecode",
        "SaveImage",
    } <= class_types
    # Al menos 6 nodos (dos CLIPTextEncode: positivo y negativo).
    assert len(graph) >= 6


def test_build_workflow_sustituye_los_parametros():
    graph = build_workflow(**_BASE)
    # Checkpoint.
    ckpt = next(n for n in graph.values() if n["class_type"] == "CheckpointLoaderSimple")
    assert ckpt["inputs"]["ckpt_name"] == _BASE["ckpt_name"]
    # Prompt positivo y negativo.
    texts = [
        n["inputs"]["text"] for n in graph.values() if n["class_type"] == "CLIPTextEncode"
    ]
    assert _BASE["prompt"] in texts
    assert _BASE["negative"] in texts
    # Latente.
    latent = next(n for n in graph.values() if n["class_type"] == "EmptyLatentImage")
    assert latent["inputs"]["width"] == _BASE["width"]
    assert latent["inputs"]["height"] == _BASE["height"]
    # KSampler.
    ksampler = next(n for n in graph.values() if n["class_type"] == "KSampler")
    assert ksampler["inputs"]["seed"] == _BASE["seed"]
    assert ksampler["inputs"]["steps"] == _BASE["steps"]
    assert ksampler["inputs"]["cfg"] == _BASE["cfg"]
    assert ksampler["inputs"]["sampler_name"] == _BASE["sampler"]
    assert ksampler["inputs"]["scheduler"] == _BASE["scheduler"]


def test_build_workflow_nodos_conectados():
    graph = build_workflow(**_BASE)
    ksampler = next(n for n in graph.values() if n["class_type"] == "KSampler")
    # El KSampler referencia model/positive/negative/latent como [id, idx].
    for key in ("model", "positive", "negative", "latent_image"):
        ref = ksampler["inputs"][key]
        assert isinstance(ref, list) and len(ref) == 2
        assert ref[0] in graph  # el nodo referenciado existe


def test_build_workflow_acepta_refs_sin_alterar_el_grafo():
    base_graph = build_workflow(**_BASE)
    with_refs = build_workflow(
        **_BASE, style_refs=["estilo.png"], char_refs=["heroe.png"]
    )
    # style_refs/char_refs son placeholders (punto 5): no cambian el grafo todavía.
    assert with_refs == base_graph
    # Y con None explícito tampoco.
    assert build_workflow(**_BASE, style_refs=None, char_refs=None) == base_graph


# --------------------------------------------------------------------------- #
# build_workflow (plantilla externa)
# --------------------------------------------------------------------------- #


def test_build_workflow_desde_plantilla(tmp_path):
    template = {
        "10": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "{{ckpt_name}}"},
        },
        "11": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "{{prompt}}", "clip": ["10", 1]},
        },
        "12": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": "{{width}}", "height": "{{height}}", "batch_size": 1},
        },
        "13": {
            "class_type": "KSampler",
            "inputs": {
                "seed": "{{seed}}",
                "steps": "{{steps}}",
                "cfg": "{{cfg}}",
                "sampler_name": "{{sampler}}",
                "scheduler": "{{scheduler}}",
            },
        },
    }
    path = tmp_path / "mi_workflow.json"
    path.write_text(json.dumps(template), encoding="utf-8")

    graph = build_workflow(**_BASE, template_path=str(path))

    assert graph["10"]["inputs"]["ckpt_name"] == _BASE["ckpt_name"]
    assert graph["11"]["inputs"]["text"] == _BASE["prompt"]
    # Los numéricos conservan su tipo (no quedan como string).
    assert graph["12"]["inputs"]["width"] == _BASE["width"]
    assert graph["12"]["inputs"]["height"] == _BASE["height"]
    assert graph["13"]["inputs"]["seed"] == _BASE["seed"]
    assert graph["13"]["inputs"]["steps"] == _BASE["steps"]
    assert graph["13"]["inputs"]["cfg"] == _BASE["cfg"]
    assert graph["13"]["inputs"]["sampler_name"] == _BASE["sampler"]
    assert graph["13"]["inputs"]["scheduler"] == _BASE["scheduler"]


def test_build_workflow_plantilla_inexistente_usa_embebido(tmp_path):
    # Si la ruta no existe, se usa el grafo embebido (no revienta).
    graph = build_workflow(**_BASE, template_path=str(tmp_path / "noexiste.json"))
    assert graph == build_workflow(**_BASE)


# --------------------------------------------------------------------------- #
# base_size_for_aspect
# --------------------------------------------------------------------------- #


def test_base_size_for_aspect_16_9():
    w, h = base_size_for_aspect("16:9", 640)
    assert max(w, h) == 640
    assert w % 8 == 0 and h % 8 == 0
    assert w > h
    # Proporción ~16:9.
    assert abs((w / h) - (16 / 9)) < 0.05


def test_base_size_for_aspect_9_16():
    w, h = base_size_for_aspect("9:16", 640)
    assert max(w, h) == 640
    assert w % 8 == 0 and h % 8 == 0
    assert h > w
    assert abs((h / w) - (16 / 9)) < 0.05


def test_base_size_for_aspect_1_1():
    w, h = base_size_for_aspect("1:1", 512)
    assert (w, h) == (512, 512)
    assert w % 8 == 0 and h % 8 == 0


def test_base_size_for_aspect_minimo_8():
    w, h = base_size_for_aspect("16:9", 8)
    assert w >= 8 and h >= 8
    assert w % 8 == 0 and h % 8 == 0


# --------------------------------------------------------------------------- #
# build_generation_meta
# --------------------------------------------------------------------------- #

_META_KEYS = {
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
}


def test_build_generation_meta_shape_completo():
    meta = build_generation_meta(
        provider="sd_local",
        model="v1-5-pruned-emaonly.safetensors",
        prompt="x",
        negative_prompt="y",
        seed=1,
        width=640,
        height=360,
        steps=20,
        sampler="euler",
        cfg=7.0,
        generation_time_s=1.2,
        status="ok",
        workflow_version="sd15-embedded-v1",
        workflow_hash="sha256:" + "0" * 64,
    )
    assert set(meta.keys()) == _META_KEYS
    assert meta["status"] in {"ok", "fallback"}
    assert meta["workflow_version"] == "sd15-embedded-v1"
    # fallback_reason no provisto => None.
    assert meta["fallback_reason"] is None


def test_build_generation_meta_claves_ausentes_en_none():
    meta = build_generation_meta(provider="sd_local", status="fallback")
    assert set(meta.keys()) == _META_KEYS
    for key in _META_KEYS - {"provider", "status"}:
        assert meta[key] is None, key
    assert meta["status"] == "fallback"


def test_build_generation_meta_deriva_hash_del_grafo():
    graph = build_workflow(**_BASE)
    meta = build_generation_meta(
        provider="sd_local",
        status="ok",
        workflow_version=workflow_version(None),
        graph=graph,
    )
    assert meta["workflow_hash"] == workflow_hash(graph)
    assert meta["workflow_version"] == "sd15-embedded-v1"


# --------------------------------------------------------------------------- #
# workflow_hash
# --------------------------------------------------------------------------- #


def test_workflow_hash_formato():
    h = workflow_hash(build_workflow(**_BASE))
    assert h.startswith("sha256:")
    digest = h.split(":", 1)[1]
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_workflow_hash_invariante_al_orden_de_claves():
    graph_a = {"a": 1, "b": {"x": 1, "y": 2}}
    graph_b = {"b": {"y": 2, "x": 1}, "a": 1}
    assert workflow_hash(graph_a) == workflow_hash(graph_b)


def test_workflow_hash_determinista():
    a = workflow_hash(build_workflow(**_BASE))
    b = workflow_hash(build_workflow(**_BASE))
    assert a == b


def test_workflow_hash_sensible_a_parametros():
    base = workflow_hash(build_workflow(**_BASE))
    variations = {
        "seed": {**_BASE, "seed": 9999},
        "steps": {**_BASE, "steps": 30},
        "cfg": {**_BASE, "cfg": 8.5},
        "sampler": {**_BASE, "sampler": "dpmpp_2m"},
        "prompt": {**_BASE, "prompt": "otro prompt"},
        "ckpt_name": {**_BASE, "ckpt_name": "otro.safetensors"},
        "width": {**_BASE, "width": 512},
    }
    for name, params in variations.items():
        assert workflow_hash(build_workflow(**params)) != base, name


# --------------------------------------------------------------------------- #
# workflow_version
# --------------------------------------------------------------------------- #


def test_workflow_version_embebido():
    assert workflow_version(None) == "sd15-embedded-v1"
    assert workflow_version("") == "sd15-embedded-v1"


def test_workflow_version_plantilla():
    assert workflow_version("/ruta/a/mi_wf.json") == "custom:mi_wf"
    assert workflow_version("retrato_cinematico.json") == "custom:retrato_cinematico"


# --------------------------------------------------------------------------- #
# ComfyUICapabilities
# --------------------------------------------------------------------------- #


def test_default_capabilities():
    caps = default_capabilities()
    assert isinstance(caps, ComfyUICapabilities)
    assert caps.supports_system_stats is True
    assert caps.supports_queue is True
    assert caps.supports_free is True
    assert caps.version is None
    assert default_capabilities("0.3.0").version == "0.3.0"


# --------------------------------------------------------------------------- #
# Settings nuevos (sin .env)
# --------------------------------------------------------------------------- #


def test_settings_expone_sd_y_ollama_defaults():
    s = Settings()
    assert s.sd_checkpoint == "v1-5-pruned-emaonly.safetensors"
    assert s.sd_steps == 20
    assert s.sd_sampler == "euler"
    assert s.sd_scheduler == "normal"
    assert s.sd_cfg == 7.0
    assert s.sd_base_size == 640
    assert isinstance(s.sd_negative_prompt, str) and s.sd_negative_prompt
    assert s.sd_lowvram is True
    assert s.sd_workflow_path == ""
    assert s.sd_timeout_s == 600
    assert s.sd_poll_interval_s == 1.5
    assert s.sd_connect_timeout_s == 5.0
    assert s.sd_max_retries == 1
    assert s.sd_free_after is True
    assert s.sd_free_before is False
    # Concurrencia: límite inicial 1 (protege los 4 GB de VRAM).
    assert s.sd_max_concurrency == 1
    assert s.ollama_unload_between_stages is True
    assert s.ollama_unload_verify is True
    assert s.ollama_unload_timeout_s == 30.0
    assert s.ollama_unload_poll_interval_s == 1.0
