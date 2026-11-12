"""Proveedores LLM reales (FEAT-003).

``OllamaLLM`` es el default local/gratuito (llama a Ollama vía HTTP). ``OpenAILLM``
es un stub pluggable que solo opera si hay ``OPENAI_API_KEY``. Ambos se registran
en el registry de proveedores bajo ``"ollama"`` / ``"openai"``.
"""

from __future__ import annotations

from app.providers.llm.ollama import OllamaLLM
from app.providers.llm.openai import OpenAILLM

__all__ = ["OllamaLLM", "OpenAILLM"]
