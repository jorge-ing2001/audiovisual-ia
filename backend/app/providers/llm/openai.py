"""``OpenAILLM`` — stub pluggable del proveedor LLM de pago (opcional).

Solo es utilizable si hay ``OPENAI_API_KEY`` configurada. La integración completa
se deja para una feature posterior (Fase 4); aquí se registra como punto de
extensión conmutable sin tocar motores. Construirlo sin clave falla con un mensaje
accionable para evitar "simular" una respuesta.
"""

from __future__ import annotations

from app.config import get_settings


class OpenAILLM:
    """Stub del ``LLMProvider`` de OpenAI (requiere ``OPENAI_API_KEY``)."""

    name = "openai"

    def __init__(self, *, api_key: str | None = None, model: str = "gpt-4o-mini") -> None:
        self.api_key = api_key or get_settings().openai_api_key
        self.model = model
        if not self.api_key:
            raise RuntimeError(
                "OpenAILLM requiere OPENAI_API_KEY. Configúrala en tu .env o usa el "
                "backend por defecto 'ollama' (AIV_LLM_BACKEND=ollama)."
            )

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict | None = None,
        **opts: object,
    ) -> str:
        raise NotImplementedError(
            "La integración de OpenAI se implementa en una feature posterior. "
            "El backend LLM por defecto local/gratuito es 'ollama'."
        )
