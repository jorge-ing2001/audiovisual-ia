"""Backend opcional de voz propia grabada (``RecordedVoiceEngine``).

Cubre el caso "usa mi propia voz" además del TTS generado: el usuario sube un audio
grabado y el motor produce los **mismos artefactos** que el camino TTS
(``Asset(kind='voice')`` + ``.srt`` + ``palabras.json``), aplicando:

  1. Limpieza + masterización a ~-14 LUFS con ``engines/voice/master.py``.
  2. (Opcional) recorte de silencios con re-mapeo de timings (``engines/voice/trim.py``).
  3. ASR con el ``ASRProvider`` del registry (faster-whisper por defecto) para obtener
     los ``WordTiming``.
  4. Subtítulos por línea (``.srt``) y palabra-por-palabra (``palabras.json``).

Es pluggable: se usa cuando ``AIV_TTS_BACKEND=recorded`` o llamando directamente a este
motor con la ruta del audio subido. No se acopla a la estructura ``projects/<slug>`` ni
a Audacity (``.aup3`` queda fuera del alcance del MVP). El reconocimiento del habla se
reimplementa de forma propia inspirada en ``voz.py`` de skills-videos-cortos (MIT).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.engines.voice.master import CleanupOptions, VoiceMaster
from app.engines.voice.subtitles import (
    apply_corrections,
    cues_from_word_timings,
    write_srt,
    write_words_json,
)
from app.engines.voice.trim import SilenceTrimmer
from app.models.entities import Asset, Project
from app.providers import registry
from app.providers.base import WordTiming
from app.storage.media import ensure_project_workspace


@dataclass
class RecordedVoiceResult:
    """Resultado de procesar una voz grabada: Assets persistidos y rutas."""

    voice_asset_id: int | None = None
    srt_asset_id: int | None = None
    words_path: str | None = None
    timings: list[WordTiming] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


class RecordedVoiceEngine:
    """Procesa una grabación propia -> voz masterizada + subtítulos + palabras.json."""

    def process(
        self,
        project: Project,
        session: Session,
        recorded_path: str,
        *,
        corrections: dict[str, str] | None = None,
        max_words_per_cue: int = 7,
    ) -> RecordedVoiceResult:
        """Procesa ``recorded_path`` y persiste los mismos Assets que el camino TTS.

        Devuelve un ``RecordedVoiceResult``. Si el ASR no está disponible, persiste la
        voz masterizada igualmente y deja los subtítulos vacíos (no inventa tiempos).
        """
        settings = get_settings()
        workspace = ensure_project_workspace(project.id)
        voice_dir = workspace / "voice"
        srt_dir = workspace / "srt"

        # 1) Limpieza + masterización a ~-14 LUFS.
        mastered = Path(voice_dir) / "recorded_master.wav"
        master = VoiceMaster()
        cleanup = CleanupOptions(enabled=settings.voice_cleanup)
        master.master_file(recorded_path, str(mastered), cleanup=cleanup)
        final_audio = str(mastered)
        duration_source = final_audio

        # 2) ASR sobre el audio masterizado.
        timings: list[WordTiming] = []
        asr_provider = ""
        try:
            asr = registry.resolve("asr", project.provider_overrides)
            asr_provider = getattr(asr, "name", "asr")
            timings = asr.transcribe(final_audio)
        except Exception as exc:  # modelo/dep no disponible: no inventar tiempos
            from app.providers.asr.faster_whisper import AsrNotAvailable

            if not isinstance(exc, AsrNotAvailable):
                raise
            timings = []

        # 3) Recorte de silencios opcional (re-mapea los timings al nuevo eje).
        if settings.trim_silence and timings:
            trimmed = Path(voice_dir) / "recorded_trimmed.wav"
            trimmer = SilenceTrimmer()
            trim_result = trimmer.trim(
                final_audio,
                str(trimmed),
                timings,
                silence_db=settings.trim_silence_db,
                min_silence=settings.trim_min_silence,
            )
            final_audio = trim_result.path
            timings = trim_result.timings
            duration_source = final_audio

        # 4) Diccionario de correcciones (reparte tiempos proporcionalmente).
        if corrections:
            timings = apply_corrections(timings, corrections)

        duration_s = self._probe_duration(duration_source, master)

        # Persistir el Asset de voz (mismo kind que el camino TTS).
        voice_asset = Asset(
            project_id=project.id,
            scene_id=None,
            kind="voice",
            path=final_audio,
            provider="recorded",
            meta={"duration_s": duration_s, "source": "recorded"},
        )
        session.add(voice_asset)
        session.flush()

        result = RecordedVoiceResult(
            voice_asset_id=voice_asset.id,
            timings=timings,
            meta={"duration_s": duration_s, "asr_provider": asr_provider},
        )

        # 5) Subtítulos (.srt por línea) + palabras.json (palabra-por-palabra).
        if timings:
            cues = cues_from_word_timings(timings, max_words_per_cue=max_words_per_cue)
            srt_path = Path(srt_dir) / "recorded.srt"
            write_srt(cues, str(srt_path))
            words_path = Path(srt_dir) / "palabras.json"
            write_words_json(timings, str(words_path))

            srt_asset = Asset(
                project_id=project.id,
                scene_id=None,
                kind="srt",
                path=str(srt_path),
                provider=asr_provider or "recorded",
                meta={"cues": len(cues), "words_json": str(words_path)},
            )
            session.add(srt_asset)
            session.flush()
            result.srt_asset_id = srt_asset.id
            result.words_path = str(words_path)

        session.commit()
        return result

    @staticmethod
    def _probe_duration(path: str, master: VoiceMaster) -> float:
        """Duración del audio final (via ffprobe), 0.0 si no se puede medir."""
        trimmer = SilenceTrimmer(ffmpeg_bin=master.ffmpeg, ffprobe_bin=master.ffprobe)
        try:
            return round(trimmer._probe_duration(path), 3)
        except Exception:
            return 0.0


def _available() -> bool:
    """El ffmpeg configurado existe (para que el motor pueda operar)."""
    return shutil.which(get_settings().ffmpeg_bin) is not None
