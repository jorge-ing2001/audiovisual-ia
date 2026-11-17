"""Configuración centralizada (Pydantic Settings).

Lee las variables ``AIV_*`` documentadas en ``docs/LOCAL-FREE-STACK.md`` y ``.env.example``.
Sin ninguna clave de pago el sistema funciona con los defaults locales/gratuitos:
LLM=ollama, imágenes=stock (con degradación a placeholder), TTS=kokoro, ASR=faster_whisper.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la aplicación resuelta desde variables de entorno / ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="AIV_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Selección de backend por capacidad ---
    llm_backend: str = "ollama"            # ollama | openai
    image_backend: str = "stock"           # stock | placeholder | sd_local | stability | openai
    tts_backend: str = "kokoro"            # kokoro | edge | elevenlabs | openai | recorded
    asr_backend: str = "faster_whisper"    # faster_whisper

    # --- Voz/Audio (motor 5, reutiliza lemo-opuscar MIT) ---
    # Voz por defecto para Kokoro (vacío = default del script, af_bella / zf_xiaoxiao).
    tts_voice: str = ""
    # Voz por defecto para edge-tts (vacío = default del script, zh-CN-XiaoxiaoNeural).
    edge_voice: str = ""
    # HOME alternativo para la caché de espeak-ng de Kokoro cuando $HOME es de solo lectura.
    tts_cache_home: str = ""
    # Modelo de faster-whisper (nombre HF o ruta local) e idioma para subtítulos.
    asr_model: str = "base"
    asr_language: str = ""
    # Recorte de silencios con re-mapeo de timings (tecnica adaptada de voz.py, MIT).
    # Desactivado por defecto para no alterar la alineacion voz/subtitulos sin pedirlo.
    trim_silence: bool = False
    trim_silence_db: float = -35.0
    trim_min_silence: float = 0.35
    # Limpieza de voz (highpass/de-noise/de-esser/compresor) antes de masterizar.
    voice_cleanup: bool = True

    # --- LLM local (Ollama) ---
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    # --- Motor de generación de imágenes (stock gratuito; claves gratuitas, opcionales) ---
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    # Orden de búsqueda entre proveedores de stock (CSV): por defecto pixabay y luego pexels.
    image_provider_order: str = "pixabay,pexels"
    # Degradación cuando stock no tiene clave/red/resultados.
    image_fallback: str = "placeholder"

    # --- Generación local futura (Stable Diffusion); declarado pero sin implementar ---
    sd_local_enabled: bool = False
    sd_local_backend: str = "comfyui"
    sd_local_url: str = "http://localhost:8188"
    # Parámetros del grafo SD 1.5 embebido / plantilla de ComfyUI (Bloque 3).
    sd_checkpoint: str = "v1-5-pruned-emaonly.safetensors"
    sd_steps: int = 20
    sd_sampler: str = "euler"
    sd_scheduler: str = "normal"
    sd_cfg: float = 7.0
    # Lado largo (px) de la LATENTE de generación; se escala por aspecto a múltiplos de 8.
    # Bajo a propósito (640) para caber en los 4 GB de VRAM; el upscale es posterior.
    sd_base_size: int = 640
    # Negativo por defecto razonable para SD 1.5 (evita artefactos comunes).
    sd_negative_prompt: str = (
        "lowres, bad anatomy, bad hands, text, error, missing fingers, "
        "extra digit, fewer digits, cropped, worst quality, low quality, "
        "jpeg artifacts, signature, watermark, username, blurry"
    )
    # Modo bajo consumo de VRAM de ComfyUI (--lowvram); recomendado para 4 GB.
    sd_lowvram: bool = True
    # Ruta a un workflow JSON (formato API de ComfyUI) con marcadores {{...}}.
    # Vacío = usar el grafo SD 1.5 embebido.
    sd_workflow_path: str = ""
    # Timeouts y reintentos de la parte de red (se consumen en FEAT-003).
    sd_timeout_s: int = 600
    sd_poll_interval_s: float = 1.5
    sd_connect_timeout_s: float = 5.0
    sd_max_retries: int = 1
    # Liberación de VRAM de ComfyUI (POST /free) antes/después de generar.
    sd_free_after: bool = True
    sd_free_before: bool = False
    # Concurrencia máxima de generaciones SD simultáneas. Límite inicial = 1 para no
    # saturar los 4 GB de VRAM; ampliable por config si el hardware lo permite.
    sd_max_concurrency: int = 1
    # Descarga del modelo de Ollama entre etapas (libera VRAM para SD y viceversa).
    ollama_unload_between_stages: bool = True
    ollama_unload_verify: bool = True
    ollama_unload_timeout_s: float = 30.0
    ollama_unload_poll_interval_s: float = 1.0

    # --- Motor audiovisual ---
    video_engine: str = "remotion"
    video_render_engine: str = "ffmpeg"
    # Directorio del proyecto Remotion (composiciones + scripts de build/render).
    # Vacío = se resuelve al ``remotion/`` del monorepo (hermano de ``backend/``).
    remotion_dir: str = ""
    # Binario de Node para invocar los scripts de Remotion (configurable).
    node_bin: str = "node"
    fps: int = 30

    # --- Binarios externos (configurables: en el sandbox viven en /projects/sandbox/.localbin) ---
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    # --- Motor 3D / Blender (Fase 3) ---
    # Proveedor de la capacidad "clip" (clip de vídeo por escena); futuro: veo | runway | kling.
    clip_backend: str = "blender"
    # Ruta/nombre del binario de Blender 4.x (configurable, como AIV_FFMPEG_BIN;
    # en el PC del usuario se apunta a Blender 4.x; en el sandbox no está disponible).
    blender_bin: str = "blender"
    # Motor de render de Blender: eevee (rápido, MVP) | cycles (lento, opcional).
    render_engine_3d: str = "eevee"
    # Carpeta de assets 3D (personajes/sets/animaciones). Vacío = media/assets3d.
    blender_assets_dir: str = ""
    # Muestras de render (EEVEE/Cycles); sube calidad a costa de tiempo.
    blender_samples: int = 32
    # Timeout del subproceso de Blender por escena (el tiempo de render no es restricción).
    blender_timeout_s: int = 3600
    # Default global de Project.production_type para proyectos nuevos: 2d | 3d
    # (override por proyecto en BD).
    production_type: str = "2d"

    # --- Reutilización de lemo-opuscar (voz/ASR/mux, código MIT) ---
    lemo_opuscar_path: str = ""

    # --- Proveedores de pago (opcionales; solo si se activan arriba) ---
    openai_api_key: str = ""
    elevenlabs_api_key: str = ""
    stability_api_key: str = ""

    # --- Almacenamiento ---
    database_url: str = "sqlite:///./audiovisual.db"
    media_root: str = "./media"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Devuelve la instancia (cacheada) de configuración."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
