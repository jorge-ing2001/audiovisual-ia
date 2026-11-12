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
    tts_backend: str = "kokoro"            # kokoro | edge | elevenlabs | openai
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

    # --- Motor audiovisual ---
    video_engine: str = "remotion"
    video_render_engine: str = "ffmpeg"

    # --- Binarios externos (configurables: en el sandbox viven en /projects/sandbox/.localbin) ---
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

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
