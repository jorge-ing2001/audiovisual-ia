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

from app.engines.voice.subtitles import (
    Cue,
    apply_corrections,
    cues_from_text,
    cues_from_word_timings,
    write_srt,
    write_words_json,
)
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
        corrections: dict[str, str] | None = None,
    ) -> Asset | None:
        """Genera subtítulos ``.srt`` del proyecto, sincronizados con el audio por escena.

        Camino preferente (ASR real): transcribe cada WAV de voz (en orden de escena),
        desplaza los tiempos por el offset acumulado de las líneas previas, aplica el
        diccionario de correcciones (si se pasa) y escribe un único ``.srt`` por línea más
        un ``palabras.json`` (palabra-por-palabra) con tiempos reales del ASR.

        Fallback DETERMINISTA (sin ASR): cuando faster-whisper/su modelo no están
        disponibles se derivan los cues del TEXTO de narración de cada escena, repartidos
        proporcionalmente en la duración de la escena (``cues_from_text``). No se inventa
        una transcripción: el Asset queda marcado ``provider='text-derived'`` para que sea
        auditable y nunca se confunda con ASR real. Así los subtítulos SIEMPRE llegan al
        timeline y se queman en el MP4, también en modo local/gratuito sin ASR.

        Devuelve ``None`` solo si no hay voz ni texto del que derivar subtítulos.
        """
        voice_assets = [a for a in project.assets if a.kind == "voice"]
        if not voice_assets:
            return None

        all_words, asr_ok = self._asr_word_timings(project, voice_assets)

        if asr_ok:
            if corrections:
                all_words = apply_corrections(all_words, corrections)
            all_cues = cues_from_word_timings(
                all_words, max_words_per_cue=max_words_per_cue
            )
            provider = "faster_whisper"
            source = "asr"
        else:
            # Sin ASR: derivar del texto de narración por escena (determinista).
            all_cues = self._text_cues(project, voice_assets, max_words_per_cue)
            all_words = []
            provider = "text-derived"
            source = "text"

        if not all_cues:
            return None

        srt_dir = ensure_project_workspace(project.id) / "srt"
        srt_path = Path(srt_dir) / "subtitles.srt"
        write_srt(all_cues, str(srt_path))
        words_path = Path(srt_dir) / "palabras.json"
        write_words_json(all_words, str(words_path))

        asset = Asset(
            project_id=project.id,
            scene_id=None,
            kind="srt",
            path=str(srt_path),
            provider=provider,
            meta={
                "cues": len(all_cues),
                "words_json": str(words_path),
                "source": source,
            },
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset

    def _asr_word_timings(self, project: Project, voice_assets: list[Asset]):
        """Transcribe las voces con el ASR activo. Devuelve ``(words, asr_ok)``.

        ``asr_ok=False`` cuando faster-whisper/su modelo no están disponibles, para que la
        capa superior use el fallback por texto en vez de inventar tiempos.
        """
        from app.providers.asr.faster_whisper import AsrNotAvailable
        from app.providers.base import WordTiming

        asr = registry.resolve("asr", project.provider_overrides)
        all_words: list[WordTiming] = []
        offset = 0.0
        try:
            for asset in sorted(voice_assets, key=lambda a: a.id):
                timings = asr.transcribe(asset.path)
                for w in timings:
                    all_words.append(
                        WordTiming(
                            word=w.word,
                            start_s=w.start_s + offset,
                            end_s=w.end_s + offset,
                        )
                    )
                offset += float((asset.meta or {}).get("duration_s", 0.0))
        except AsrNotAvailable:
            return [], False
        return all_words, True

    def _text_cues(
        self, project: Project, voice_assets: list[Asset], max_words_per_cue: int
    ) -> list[Cue]:
        """Deriva cues del texto de narración de cada escena (fallback sin ASR).

        Usa la duración real de la voz de la escena (``Asset(kind='voice').meta
        ['duration_s']``) y su offset acumulado para que los cues queden en coordenadas
        globales del proyecto (como el ``.srt`` del ASR), de modo que ``TimelineBuilder``
        los reparta por escena igual que con el ASR real.
        """
        voice_by_scene: dict[int, Asset] = {}
        for asset in sorted(voice_assets, key=lambda a: a.id):
            if asset.scene_id is not None:
                voice_by_scene[asset.scene_id] = asset

        scenes = sorted(project.scenes, key=lambda s: (s.order, s.id))
        cues: list[Cue] = []
        offset = 0.0
        for scene in scenes:
            voice_asset = voice_by_scene.get(scene.id)
            if voice_asset is None:
                # Sin voz en la escena: no hay audio con el que sincronizar el texto.
                continue
            duration = float((voice_asset.meta or {}).get("duration_s", 0.0))
            if duration <= 0:
                continue
            text = (scene.narration_text or "").strip()
            if text:
                cues.extend(
                    cues_from_text(
                        text,
                        start_s=offset,
                        duration_s=duration,
                        max_words_per_cue=max_words_per_cue,
                    )
                )
            offset += duration
        return cues
