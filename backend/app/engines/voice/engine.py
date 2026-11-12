"""Motor 5 — Voz/Audio (``VoiceEngine``).

Sintetiza la narración de un proyecto con el ``TTSProvider`` activo del registry
(default ``kokoro`` offline con degradación a ``edge`` online), persiste un
``Asset(kind='voice')`` por línea, registra música/SFX como ``Asset(kind='music'|'sfx')``
y genera subtítulos (``Asset(kind='srt')``) a partir de los tiempos del ``ASRProvider``.

El motor NO conoce Kokoro, edge-tts ni faster-whisper directamente: todo pasa por
``registry.resolve(...)`` (invariante de la §3). La reutilización del código MIT de
lemo-opuscar ocurre dentro de los proveedores; aquí se orquesta y se persiste.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.engines.voice.subtitles import cues_from_word_timings, write_srt
from app.models.entities import Asset, Project
from app.providers import registry
from app.providers.base import AudioAsset, VoiceLine
from app.providers.tts._lemo import LemoNotAvailable
from app.storage.media import ensure_project_workspace


@dataclass
class VoiceResult:
    """Resultado de sintetizar la narración de un proyecto."""

    asset_ids: list[int]
    provider: str
    degraded: bool = False


class VoiceEngine:
    """Motor de voz/audio: líneas de narración -> WAV + Assets; subtítulos vía ASR."""

    # --- síntesis de narración --------------------------------------------- #

    def synthesize(
        self, lines: list[VoiceLine], out_dir: str, *, project_overrides: dict | None = None
    ) -> list[AudioAsset]:
        """Sintetiza ``lines`` con el TTS activo, degradando ``kokoro->edge`` si falta el modelo.

        Devuelve los ``AudioAsset`` (no persiste): persistir es responsabilidad de
        ``synthesize_project``. Si el backend activo no es ``kokoro`` no se degrada:
        el fallo se propaga para no ocultar un problema de configuración.
        """
        backend = registry.active_backend("tts", project_overrides)
        provider = registry.resolve("tts", project_overrides)
        try:
            return provider.synthesize(lines, out_dir)
        except LemoNotAvailable:
            if backend != "kokoro":
                raise
            # Degradación kokoro -> edge (§3): el modelo offline no está disponible.
            edge = registry.PROVIDERS["tts"]["edge"]()
            return edge.synthesize(lines, out_dir)

    def synthesize_project(
        self, project: Project, session: Session
    ) -> VoiceResult:
        """Sintetiza la narración de todas las escenas del proyecto y persiste Assets.

        Una línea de narración por escena (en orden). Cada WAV produce un
        ``Asset(kind='voice')`` ligado a su escena, con la duración en ``meta``.
        """
        scenes = sorted(project.scenes, key=lambda s: s.order)
        narration = [s for s in scenes if (s.narration_text or "").strip()]
        if not narration:
            return VoiceResult(asset_ids=[], provider="", degraded=False)

        language = project.scripts[-1].language if project.scripts else "es"
        lines = [
            VoiceLine(text=scene.narration_text.strip(), language=language)
            for scene in narration
        ]

        out_dir = ensure_project_workspace(project.id) / "voice"
        backend = registry.active_backend("tts", project.provider_overrides)
        audio = self.synthesize(
            lines, str(out_dir), project_overrides=project.provider_overrides
        )
        degraded = bool(audio) and audio[0].provider != backend

        asset_ids: list[int] = []
        for scene, clip in zip(narration, audio):
            asset = Asset(
                project_id=project.id,
                scene_id=scene.id,
                kind="voice",
                path=clip.path,
                provider=clip.provider,
                meta={
                    "duration_s": clip.duration_s,
                    "sample_rate": clip.sample_rate,
                    **(clip.meta or {}),
                },
            )
            session.add(asset)
            session.flush()
            asset_ids.append(asset.id)
        session.commit()
        return VoiceResult(
            asset_ids=asset_ids,
            provider=audio[0].provider if audio else backend,
            degraded=degraded,
        )

    # --- música / SFX ------------------------------------------------------- #

    def register_audio_asset(
        self,
        project: Project,
        session: Session,
        *,
        kind: str,
        path: str,
        provider: str = "local",
        meta: dict | None = None,
    ) -> Asset:
        """Registra un ``Asset`` de música o SFX (p. ej. recursos MIT libres).

        No sintetiza: enlaza un fichero de audio existente (copiado al workspace del
        proyecto) como ``Asset(kind='music'|'sfx')`` para que la mezcla y el timeline
        lo consuman.
        """
        if kind not in ("music", "sfx"):
            raise ValueError("kind debe ser 'music' o 'sfx'")
        asset = Asset(
            project_id=project.id,
            scene_id=None,
            kind=kind,
            path=path,
            provider=provider,
            meta=meta or {},
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset

    # --- subtítulos --------------------------------------------------------- #

    def generate_subtitles(
        self,
        project: Project,
        session: Session,
        *,
        max_words_per_cue: int = 7,
    ) -> Asset | None:
        """Genera subtítulos ``.srt`` del proyecto a partir de los tiempos del ASR.

        Transcribe cada WAV de voz (en orden de escena), desplaza los tiempos por el
        offset acumulado de las líneas previas y escribe un único ``.srt``. Devuelve
        ``None`` (sin persistir) si el ASR no está disponible, en vez de inventar
        tiempos.
        """
        voice_assets = [a for a in project.assets if a.kind == "voice"]
        if not voice_assets:
            return None

        asr = registry.resolve("asr", project.provider_overrides)

        all_cues = []
        offset = 0.0
        try:
            for asset in sorted(voice_assets, key=lambda a: a.id):
                timings = asr.transcribe(asset.path)
                cues = cues_from_word_timings(
                    timings, max_words_per_cue=max_words_per_cue
                )
                for cue in cues:
                    cue.t0 += offset
                    cue.t1 += offset
                all_cues.extend(cues)
                offset += float((asset.meta or {}).get("duration_s", 0.0))
        except Exception as exc:  # faster-whisper/modelo no disponible: no inventar
            from app.providers.asr.faster_whisper import AsrNotAvailable

            if isinstance(exc, AsrNotAvailable):
                return None
            raise

        if not all_cues:
            return None

        srt_dir = ensure_project_workspace(project.id) / "srt"
        srt_path = Path(srt_dir) / "subtitles.srt"
        write_srt(all_cues, str(srt_path))

        asset = Asset(
            project_id=project.id,
            scene_id=None,
            kind="srt",
            path=str(srt_path),
            provider="faster_whisper",
            meta={"cues": len(all_cues)},
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset
