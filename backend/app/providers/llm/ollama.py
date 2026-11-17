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

import logging
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


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

    # ----------------------------------------------------------------------- #
    # Gestión de VRAM secuencial (Bloque 3, punto 2): unload verificado
    # ----------------------------------------------------------------------- #

    def loaded_models(self) -> list[str]:
        """Modelos actualmente cargados en Ollama (``GET {url}/api/ps``).

        Base de la VERIFICACIÓN DE ESTADO PREVIO (refinamiento 1). Devuelve los nombres
        del campo ``models[].name`` (o ``model`` como alias). TOLERANTE: si ``/api/ps``
        falla, no existe (404) o la respuesta no es interpretable, devuelve ``[]`` y lo
        registra con ``log.info`` (sin lanzar): no poder consultarlo no debe romper nada.
        """
        try:
            resp = httpx.get(f"{self.url}/api/ps", timeout=5.0)
            if getattr(resp, "status_code", None) == 404:
                logger.info(
                    "Ollama %s: /api/ps no disponible (404); no se puede listar modelos "
                    "cargados.",
                    self.url,
                )
                return []
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            logger.info(
                "Ollama %s: no se pudo consultar /api/ps (%s); se asume sin modelos "
                "cargados.",
                self.url,
                exc,
            )
            return []

        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return []
        names: list[str] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if name:
                names.append(str(name))
        return names

    def unload(self) -> None:
        """Descarga ``self.model`` de la VRAM de Ollama (``keep_alive: 0``), si procede.

        ESTADO PREVIO (refinamiento 1): primero consulta :meth:`loaded_models`; si el
        modelo NO figura cargado, lo registra y retorna (no se libera lo que no está
        cargado). Si está cargado, hace ``POST {url}/api/generate`` con
        ``{'model': self.model, 'keep_alive': 0}``.

        TOLERANTE pero SIN OCULTAR EL ERROR (refinamiento 3): cualquier excepción
        (``httpx.HTTPError``/``ConnectError``/``TimeoutException`` u otra) se registra con
        ``log.warning`` y el detalle, y se retorna sin lanzar. Idempotente.
        """
        loaded = self.loaded_models()
        if self.model not in loaded:
            logger.info(
                "Ollama %s: el modelo %s no está cargado; no hace falta liberar.",
                self.url,
                self.model,
            )
            return

        try:
            resp = httpx.post(
                f"{self.url}/api/generate",
                json={"model": self.model, "keep_alive": 0},
                timeout=min(self.timeout, 30.0),
            )
            resp.raise_for_status()
            logger.info(
                "Ollama %s: solicitada la descarga del modelo %s (keep_alive=0).",
                self.url,
                self.model,
            )
        except Exception as exc:  # noqa: BLE001 - tolerante, pero se registra el detalle
            logger.warning(
                "Fallo al liberar el modelo %s de Ollama (%s): %s; se continúa.",
                self.model,
                self.url,
                exc,
            )

    def verify_unloaded(
        self, *, timeout_s: float = 30.0, poll_interval_s: float = 1.0
    ) -> bool:
        """Verifica que Ollama descargó efectivamente ``self.model`` (``GET /api/ps``).

        Tras :meth:`unload`, consulta ``/api/ps`` en bucle hasta que el modelo ya NO
        figure cargado o se agote ``timeout_s``. Devuelve ``True`` si se confirmó la
        descarga, ``False`` si expiró. Registra el resultado (``log.info`` en éxito,
        ``log.warning`` si no se confirmó). NUNCA lanza: el objetivo es informar y no
        bloquear el pipeline. Si ``/api/ps`` no existe/falla, :meth:`loaded_models`
        degrada a ``[]`` (log), por lo que verify devuelve ``True`` de forma best-effort.
        """
        deadline = time.monotonic() + max(timeout_s, 0.0)
        interval = max(poll_interval_s, 0.0)
        while True:
            if self.model not in self.loaded_models():
                logger.info("Ollama %s: liberó el modelo %s.", self.url, self.model)
                return True
            if time.monotonic() >= deadline:
                logger.warning(
                    "Ollama %s: no confirmó la descarga de %s tras %.1fs; se continúa "
                    "igualmente.",
                    self.url,
                    self.model,
                    timeout_s,
                )
                return False
            time.sleep(interval)
