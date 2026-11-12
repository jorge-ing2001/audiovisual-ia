"""Motor 6 — Composición/Render (``RenderEngine``).

Eslabón final del pipeline: a partir del ``Timeline`` de un proyecto produce el MP4.

Flujo de ``compose``:
  1. Escribe el ``timeline.json`` en el workspace del proyecto.
  2. Invoca ``remotion/scripts/render.mjs`` (Node + @remotion/cli) pasándole el timeline
     como ``inputProps``; Remotion compone imagen a pantalla completa + subtítulos por
     escena respetando las duraciones y produce un vídeo mudo.
  3. Mezcla la pista de audio del proyecto (voz + música + SFX) a ~-14 LUFS con
     ``AudioMixer`` (reutiliza el enfoque loudnorm de 2 pasadas de lemo-opuscar, MIT).
  4. Muxea vídeo + mezcla con FFmpeg a un MP4 final (vídeo copiado, audio AAC, ``-shortest``).

El render real de Remotion usa un Chromium headless (@remotion/renderer) que exige
glibc 2.35+: cuando no está disponible, ``_run_remotion`` lanza ``RenderUnavailable`` y la
capa superior (job/test) lo trata como "render no disponible" en vez de simular un MP4.

El binario de ffmpeg/ffprobe y el directorio de Remotion se leen de la configuración
(``AIV_FFMPEG_BIN``, ``AIV_FFPROBE_BIN``, ``AIV_REMOTION_DIR``, ``AIV_NODE_BIN``).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.engines.render.timeline import Timeline, TimelineBuilder
from app.engines.voice.master import FfmpegError
from app.engines.voice.mixer import AudioMixer, MixTrack
from app.models.entities import Asset, Project
from app.storage.media import ensure_project_workspace, media_root

__all__ = [
    "RenderEngine",
    "RenderResult",
    "RenderUnavailable",
    "FfmpegError",
]


class RenderUnavailable(RuntimeError):
    """El render de Remotion no se puede ejecutar aquí (p. ej. sin Chromium/glibc)."""


@dataclass
class RenderResult:
    """Resultado del render: MP4 final, Asset persistido y artefactos intermedios."""

    video_path: str
    asset_id: int
    timeline_path: str
    silent_video_path: str
    audio_path: str | None
    meta: dict = field(default_factory=dict)


class RenderEngine:
    """Motor de composición/render: ``Timeline`` -> MP4 (Remotion + AudioMixer + FFmpeg)."""

    def __init__(
        self,
        *,
        ffmpeg_bin: str | None = None,
        ffprobe_bin: str | None = None,
        node_bin: str | None = None,
        remotion_dir: str | None = None,
    ) -> None:
        settings = get_settings()
        self.ffmpeg = ffmpeg_bin or settings.ffmpeg_bin
        self.ffprobe = ffprobe_bin or settings.ffprobe_bin
        self.node = node_bin or settings.node_bin
        self.remotion_dir = Path(remotion_dir or self._default_remotion_dir(settings))
        self.mixer = AudioMixer(ffmpeg_bin=self.ffmpeg, ffprobe_bin=self.ffprobe)

    @staticmethod
    def _default_remotion_dir(settings) -> str:
        if settings.remotion_dir:
            return settings.remotion_dir
        # backend/app/engines/render/engine.py -> sube a la raíz del monorepo.
        repo_root = Path(__file__).resolve().parents[4]
        return str(repo_root / "remotion")

    # --- API pública -------------------------------------------------------- #

    def compose(self, project: Project, session: Session) -> RenderResult:
        """Ensambla el timeline, compone con Remotion y muxea el MP4 final del proyecto.

        Persiste el ``Timeline`` en BD y un ``Asset(kind='video')`` con el MP4 resultante.
        Lanza ``RenderUnavailable`` si Remotion no puede renderizar en este entorno (sin
        simular el vídeo) y ``FfmpegError`` ante un fallo del mux.
        """
        timeline = TimelineBuilder().build(project, session, fps=get_settings().fps)
        timeline_path = self._persist_timeline(project, session, timeline)

        workspace = ensure_project_workspace(project.id) / "video"
        silent_video = workspace / "composition.mp4"
        final_video = workspace / "final.mp4"

        self._run_remotion(timeline_path, silent_video)

        audio_path = self._mix_audio(project, workspace)
        self._mux(silent_video, audio_path, final_video)

        asset = Asset(
            project_id=project.id,
            scene_id=None,
            kind="video",
            path=str(final_video),
            provider="remotion+ffmpeg",
            meta={
                "timeline_path": timeline_path,
                "silent_video": str(silent_video),
                "audio_path": audio_path,
                "duration_s": round(timeline.total_duration_s, 3),
                "width": timeline.width,
                "height": timeline.height,
                "fps": timeline.fps,
            },
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)

        return RenderResult(
            video_path=str(final_video),
            asset_id=asset.id,
            timeline_path=timeline_path,
            silent_video_path=str(silent_video),
            audio_path=audio_path,
            meta={"duration_s": round(timeline.total_duration_s, 3)},
        )

    # --- pasos -------------------------------------------------------------- #

    def _persist_timeline(
        self, project: Project, session: Session, timeline: Timeline
    ) -> str:
        """Escribe el timeline JSON en el workspace y lo persiste en la tabla Timeline."""
        from app.models.entities import Timeline as TimelineRow

        timeline_dir = ensure_project_workspace(project.id) / "timeline"
        timeline_path = timeline_dir / "timeline.json"
        # Las imágenes se emiten relativas al publicDir (= AIV_MEDIA_ROOT) para staticFile.
        payload = timeline.remotion_input_props(public_dir=str(media_root()))
        timeline_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        existing = [t for t in project.timelines]
        version = max((t.version for t in existing), default=0) + 1
        row = TimelineRow(
            project_id=project.id,
            json=timeline.model_dump(mode="json", by_alias=False),
            version=version,
        )
        session.add(row)
        session.commit()
        return str(timeline_path)

    def _run_remotion(self, timeline_path: str, out_path: Path) -> None:
        """Invoca el script de render de Remotion con el timeline como inputProps."""
        script = self.remotion_dir / "scripts" / "render.mjs"
        if not script.is_file():
            raise RenderUnavailable(
                f"No se encontró el script de render de Remotion en {script}. "
                "Configura AIV_REMOTION_DIR al directorio del proyecto Remotion."
            )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.node,
            str(script),
            "--timeline",
            str(timeline_path),
            "--out",
            str(out_path),
            "--public-dir",
            str(media_root()),
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.remotion_dir),
                capture_output=True,
                text=True,
                timeout=1200,
            )
        except FileNotFoundError as exc:
            raise RenderUnavailable(
                f"No se encontró Node ('{self.node}') para ejecutar Remotion. "
                "Configura AIV_NODE_BIN o instala Node."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RenderUnavailable("El render de Remotion excedió el tiempo límite.") from exc

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RenderUnavailable(
                f"Remotion no pudo renderizar el vídeo (código {proc.returncode}): "
                f"{detail[-800:]}"
            )
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise RenderUnavailable(
                "Remotion terminó sin producir un vídeo válido "
                f"({out_path}); render no disponible en este entorno."
            )

    def _mix_audio(self, project: Project, workspace: Path) -> str | None:
        """Mezcla voz + música + SFX del proyecto a ~-14 LUFS. Devuelve ``None`` si no hay audio."""
        tracks = self._audio_tracks(project)
        if not tracks:
            return None
        out = workspace / "mix.wav"
        result = self.mixer.mix(tracks, str(out))
        return result.path

    @staticmethod
    def _audio_tracks(project: Project) -> list[MixTrack]:
        """Construye las pistas de la mezcla: voz (orden de escena) + música + SFX."""
        voice = [a for a in project.assets if a.kind == "voice"]
        # La voz manda; música/SFX se atenúan (misma convención que el AudioMixer).
        tracks: list[MixTrack] = [
            MixTrack(path=a.path, gain=1.0)
            for a in sorted(voice, key=lambda a: a.id)
            if Path(a.path).is_file()
        ]
        for a in project.assets:
            if a.kind == "music" and Path(a.path).is_file():
                tracks.append(MixTrack(path=a.path, gain=0.3))
            elif a.kind == "sfx" and Path(a.path).is_file():
                tracks.append(MixTrack(path=a.path, gain=0.5))
        return tracks

    def _mux(self, video_path: Path, audio_path: str | None, out_path: Path) -> None:
        """Muxea vídeo + mezcla a MP4 con FFmpeg (vídeo copiado, audio AAC, ``-shortest``).

        Si no hay audio (proyecto sin voz/música), reencoda solo el vídeo a MP4. Patrón de
        ``core/render/mux.sh`` de lemo-opuscar, simplificado: la normalización de loudness
        ya la aplicó el ``AudioMixer`` sobre la mezcla.
        """
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if audio_path is None:
            args = [
                "-y", "-i", str(video_path),
                "-c:v", "copy", "-movflags", "+faststart", str(out_path),
            ]
        else:
            args = [
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "256k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(out_path),
            ]
        try:
            proc = subprocess.run(
                [self.ffmpeg, *args], capture_output=True, text=True, timeout=600
            )
        except FileNotFoundError as exc:
            raise FfmpegError(
                f"No se encontró ffmpeg en '{self.ffmpeg}'. Configura AIV_FFMPEG_BIN "
                "(en el sandbox: /projects/sandbox/.localbin/ffmpeg)."
            ) from exc
        if proc.returncode != 0:
            raise FfmpegError(
                f"ffmpeg no pudo muxear el MP4 final: {(proc.stderr or '')[-600:]}"
            )
        if not out_path.is_file() or out_path.stat().st_size == 0:
            raise FfmpegError(f"El mux no produjo un MP4 válido en {out_path}.")
