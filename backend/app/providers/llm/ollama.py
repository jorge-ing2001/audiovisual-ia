"""``OllamaLLM`` — proveedor LLM local por defecto (Ollama vía HTTP).

Llama al endpoint ``/api/generate`` de Ollama (``AIV_OLLAMA_URL``) con el modelo
``AIV_OLLAMA_MODEL`` (por defecto ``llama3.1:8b``) usando ``httpx``. Soporta salida
JSON estructurada activando ``format: "json"`` de Ollama, de modo que el motor de
guion puede parsear la respuesta directamente con Pydantic.

En el PC del usuario con Ollama corriendo y el modelo descargado, esto genera el
guion DE VERDAD. En el sandbox (sin Ollama) los tests mockean la respuesta HTTP;
nunca se simula una respuesta y se presenta como real.
"""

from __future__ import annotations

import httpx

from app.config import get_settings


class OllamaLLM:
    """Implementa ``LLMProvider`` contra un servidor Ollama local."""

    name = "ollama"

    def __init__(
        self,
        *,
        url: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        settings = get_settings()
        self.url = (url or settings.ollama_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_schema: dict | None = None,
        **opts: object,
    ) -> str:
        """Genera texto con Ollama y devuelve la respuesta cruda del modelo.

        Si se pasa ``json_schema`` se solicita a Ollama salida JSON (``format=json``)
        para que la respuesta sea parseable; el esquema concreto lo valida el motor
        que llama (con Pydantic), no el proveedor.
        """
        payload: dict[str, object] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system
        if json_schema is not None:
            payload["format"] = "json"
        options = {k: v for k, v in opts.items() if v is not None}
        if options:
            payload["options"] = options

        response = httpx.post(
            f"{self.url}/api/generate", json=payload, timeout=self.timeout
        )
        response.raise_for_status()
        data = response.json()
        return str(data.get("response", ""))
