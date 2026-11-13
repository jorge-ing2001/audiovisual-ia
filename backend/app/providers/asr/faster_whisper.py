"""``FasterWhisper`` — proveedor ASR por defecto (reutiliza el enfoque MIT de lemo-opuscar).

Sigue el patrón de ``core/tts/asr_check.py`` de lemo-opuscar: carga un ``WhisperModel``
en CPU (``device='cpu'``, ``compute_type='int8'``), remuestrea el audio a 16 kHz y
transcribe con ``word_timestamps=True`` para obtener tiempos por palabra. Devuelve una
lista de ``WordTiming`` que el motor de voz convierte en cues de subtítulos.

El modelo se descarga de Hugging Face en el primer uso; si no está disponible (sin red
ni modelo local) ``transcribe`` lanza ``AsrNotAvailable`` para que la capa superior se
salte la generación de subtítulos sin inventar tiempos. El tamaño del modelo se controla
con ``AIV_ASR_MODEL`` (default ``base``).
"""

from __future__ import annotations

from app.config import get_settings
from app.providers.base import WordTiming


class AsrNotAvailable(RuntimeError):
    """faster-whisper o su modelo no están disponibles (sin red/modelo local)."""


class FasterWhisper:
    """Implementa ``ASRProvider`` con faster-whisper (tiempos por palabra)."""

    name = "faster_whisper"

    def __init__(
        self,
        *,
        model: str | None = None,
        language: str | None = None,
        compute_type: str = "int8",
    ) -> None:
        settings = get_settings()
        self.model_name = model or settings.asr_model
        self.language = language or (settings.asr_language or None)
        self.compute_type = compute_type
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # dependencia del tier de voz no instalada
            raise AsrNotAvailable(
                "faster-whisper no está instalado: instálalo para generar subtítulos "
                "(pip install faster-whisper)."
            ) from exc
        try:
            self._model = WhisperModel(
                self.model_name, device="cpu", compute_type=self.compute_type
            )
        except Exception as exc:  # modelo no descargable / sin red
            raise AsrNotAvailable(
                f"No se pudo cargar el modelo whisper '{self.model_name}': {exc}. "
                "Se descarga de Hugging Face en el primer uso; usa un modelo local con "
                "AIV_ASR_MODEL=/ruta/al/modelo si estás sin red."
            ) from exc
        return self._model

    def transcribe(self, audio_path: str) -> list[WordTiming]:
        model = self._load()
        segments, _info = model.transcribe(
            audio_path,
            beam_size=5,
            language=self.language,
            word_timestamps=True,
        )
        timings: list[WordTiming] = []
        for segment in segments:
            for word in segment.words or []:
                timings.append(
                    WordTiming(
                        word=word.word.strip(),
                        start_s=round(float(word.start), 3),
                        end_s=round(float(word.end), 3),
                    )
                )
        return timings
