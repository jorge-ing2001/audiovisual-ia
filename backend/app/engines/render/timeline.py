"""Esquema del timeline JSON + ``TimelineBuilder`` (motor 6, FEAT-005).

El timeline es el contrato entre el backend y Remotion: una lista de escenas (imagen a
pantalla completa + subtítulos + referencia a su audio) más la resolución/fps/aspecto del
proyecto. Remotion lee este JSON como ``inputProps`` (ver ``remotion/scripts/render.mjs``)
y cada escena dura ``duration_s``; FFmpeg muxea después la mezcla de audio final.

Modelado con Pydantic para validar la forma antes de renderizar y para serializarlo con
las claves que espera la composición de Remotion (``durationInFrames``, ``imageSrc``,
``cues[{startS,endS,text}]``). ``TimelineBuilder`` ensambla el timeline a partir de las
``Scene`` y ``Asset`` persistidos de un proyecto, usando los tiempos reales de la voz
(``dur.json`` vía ``Asset.meta['duration_s']``) y los cues del ``.srt`` cuando existen.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.engines.voice.subtitles import Cue
from app.models.entities import Asset, Project, Scene
from app.providers.image.aspect import aspect_to_dims

# fps por defecto del render (coincide con el default del proyecto Remotion).
DEFAULT_FPS = 30
# Duración mínima de una escena cuando no hay voz ni duración objetivo (segundos).
MIN_SCENE_DURATION_S = 1.0


class SubtitleCue(BaseModel):
    """Un subtítulo con tiempos en segundos relativos al inicio de su escena."""

    model_config = ConfigDict(populate_by_name=True)

    start_s: float = Field(serialization_alias="startS", validation_alias="startS")
    end_s: float = Field(serialization_alias="endS", validation_alias="endS")
    text: str


class TimelineScene(BaseModel):
    """Una escena del timeline: imagen a pantalla completa + subtítulos + audio."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    duration_s: float
    duration_in_frames: int = Field(
        serialization_alias="durationInFrames", validation_alias="durationInFrames"
    )
    image_src: str | None = Field(
        default=None, serialization_alias="imageSrc", validation_alias="imageSrc"
    )
    # Clip de vídeo de la escena (producción 3D): análogo a ``image_src`` pero para un
    # ``Asset(kind='video')``. Cuando existe, Remotion dibuja ``<OffthreadVideo>`` en vez
    # de ``<Img>``; para 2d se mantiene ``image_src``.
    video_src: str | None = Field(
        default=None, serialization_alias="videoSrc", validation_alias="videoSrc"
    )
    audio_src: str | None = Field(
        default=None, serialization_alias="audioSrc", validation_alias="audioSrc"
    )
    cues: list[SubtitleCue] = Field(default_factory=list)
    narration_text: str | None = Field(
        default=None, serialization_alias="narrationText", validation_alias="narrationText"
    )
    # Placeholder en el MVP 2D: reservado para el motor de animación/cámara.
    camera_notes: str | None = Field(
        default=None, serialization_alias="cameraNotes", validation_alias="cameraNotes"
    )
    # Estilo de subtítulo EXPLÍCITO derivado del ``production_type`` del proyecto, no del
    # artefacto visual: ``"tiktok"`` en producción 3D, ``"classic"`` en 2D. Así una escena
    # 3D que degrade a imagen (o una 2D con vídeo) conserva el overlay correcto.
    subtitle_style: str = Field(
        default="classic",
        serialization_alias="subtitleStyle",
        validation_alias="subtitleStyle",
    )

    @field_validator("duration_in_frames")
    @classmethod
    def _at_least_one_frame(cls, value: int) -> int:
        return max(1, int(value))


class Timeline(BaseModel):
    """Timeline completo del proyecto que consume Remotion."""

    model_config = ConfigDict(populate_by_name=True)

    project_id: int
    fps: int = DEFAULT_FPS
    width: int
    height: int
    aspect_ratio: str = "16:9"
    scenes: list[TimelineScene] = Field(default_factory=list)

    @property
    def total_duration_s(self) -> float:
        """Duración total del timeline (suma de las escenas)."""
        return sum(scene.duration_s for scene in self.scenes)

    def remotion_input_props(self, *, public_dir: str | None = None) -> dict:
        """Serializa la forma que espera la composición de Remotion (``inputProps``).

        Solo incluye lo que consume la composición: ``fps/width/height`` y la lista de
        escenas con ``durationInFrames``, ``imageSrc``, ``cues`` y textos auxiliares.
        El audio no se envía a Remotion (lo muxea FFmpeg).

        Las rutas de imagen locales se emiten **relativas a ``public_dir``** (el
        ``publicDir`` de Remotion = ``AIV_MEDIA_ROOT``) para que la composición las cargue
        con ``staticFile`` (el navegador headless no puede abrir ``file://`` ni rutas de
        disco absolutas). Sin ``public_dir`` se dejan tal cual (p. ej. en tests de forma).
        """
        return {
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "scenes": [
                {
                    "id": scene.id,
                    "durationInFrames": scene.duration_in_frames,
                    "imageSrc": _to_static_src(scene.image_src, public_dir),
                    "videoSrc": _to_static_src(scene.video_src, public_dir),
                    "cues": [
                        {"startS": cue.start_s, "endS": cue.end_s, "text": cue.text}
                        for cue in scene.cues
                    ],
                    "narrationText": scene.narration_text,
                    "cameraNotes": scene.camera_notes,
                    "subtitleStyle": scene.subtitle_style,
                }
                for scene in self.scenes
            ],
        }


def _to_static_src(path: str | None, public_dir: str | None) -> str | None:
    """Emite el ``src`` de imagen que usa la composición (``staticFile``-compatible).

    - URLs (``http(s)://``, ``data:``) y valores vacíos: se dejan tal cual.
    - Con ``public_dir``: se devuelve la ruta **relativa** a ese directorio (POSIX), que la
      composición resolverá con ``staticFile``.
    - Sin ``public_dir``: se devuelve la ruta original (útil en tests de forma del JSON).
    """
    if not path:
        return path
    if "://" in path or path.startswith("data:"):
        return path
    if not public_dir:
        return path
    try:
        rel = Path(path).resolve().relative_to(Path(public_dir).resolve())
    except ValueError:
        # Fuera del publicDir: no se puede servir como static; se deja la ruta original.
        return path
    return rel.as_posix()


def _parse_srt_cues(srt_path: str) -> list[Cue]:
    """Lee un ``.srt`` (formato de lemo-opuscar/subtitles) y devuelve sus cues.

    Se usa para repartir los subtítulos globales del proyecto entre las escenas según
    sus ventanas temporales. Devuelve lista vacía si el fichero no existe o es ilegible.
    """
    path = Path(srt_path)
    if not path.is_file():
        return []

    def _to_seconds(stamp: str) -> float:
        stamp = stamp.strip().replace(".", ",")
        hms, _, ms = stamp.partition(",")
        parts = hms.split(":")
        if len(parts) != 3:
            return 0.0
        h, m, s = (int(p) for p in parts)
        return h * 3600 + m * 60 + s + (int(ms) / 1000 if ms else 0.0)

    cues: list[Cue] = []
    blocks = path.read_text(encoding="utf-8").strip().split("\n\n")
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # La primera línea suele ser el índice; la del tiempo contiene "-->".
        time_line = next((ln for ln in lines if "-->" in ln), None)
        if time_line is None:
            continue
        start_raw, _, end_raw = time_line.partition("-->")
        text = " ".join(
            ln for ln in lines if "-->" not in ln and not ln.strip().isdigit()
        ).strip()
        if not text:
            continue
        cues.append(Cue(t0=_to_seconds(start_raw), t1=_to_seconds(end_raw), text=text))
    return cues


class TimelineBuilder:
    """Ensambla el ``Timeline`` de un proyecto a partir de sus ``Scene`` + ``Asset``.

    Reglas de duración (en orden de prioridad por escena):
      1. Duración real de la voz de la escena (``Asset(kind='voice').meta['duration_s']``,
         que proviene del ``dur.json`` del TTS).
      2. ``Scene.duration_s`` objetivo del guion, si es > 0.
      3. ``MIN_SCENE_DURATION_S`` como último recurso (nunca una escena de 0 s).

    Los subtítulos se toman del ``.srt`` del proyecto (si existe) repartiendo cada cue en
    la escena cuya ventana temporal lo contiene y reajustando los tiempos a coordenadas
    locales de la escena. La imagen de cada escena es su ``Asset(kind='image')`` más reciente.
    """

    def build(self, project: Project, session: Session, *, fps: int = DEFAULT_FPS) -> Timeline:
        width, height = aspect_to_dims(project.aspect_ratio)

        # Estilo de subtítulo derivado del tipo de producción del proyecto (no del
        # artefacto): 3D -> TikTok, 2D -> clásico. Coherente en escenas mixtas/degradadas.
        is_3d = (project.production_type or "2d").strip().lower() == "3d"
        subtitle_style = "tiktok" if is_3d else "classic"

        scenes = sorted(project.scenes, key=lambda s: (s.order, s.id))
        assets = list(project.assets)

        image_by_scene = self._latest_by_scene(assets, kind="image")
        video_by_scene = self._latest_by_scene(assets, kind="video")
        voice_by_scene = self._latest_by_scene(assets, kind="voice")
        project_cues = self._project_cues(assets)

        timeline_scenes: list[TimelineScene] = []
        offset_s = 0.0
        for index, scene in enumerate(scenes):
            duration_s = self._scene_duration(scene, voice_by_scene.get(scene.id))
            scene_cues = self._cues_for_window(project_cues, offset_s, duration_s)

            voice_asset = voice_by_scene.get(scene.id)
            timeline_scenes.append(
                TimelineScene(
                    id=f"scene_{scene.order}_{scene.id}",
                    duration_s=round(duration_s, 3),
                    duration_in_frames=max(1, round(duration_s * fps)),
                    image_src=self._image_path(image_by_scene.get(scene.id)),
                    video_src=self._asset_path(video_by_scene.get(scene.id)),
                    audio_src=voice_asset.path if voice_asset else None,
                    cues=scene_cues,
                    narration_text=(scene.narration_text or "").strip() or None,
                    camera_notes=scene.camera_notes,
                    subtitle_style=subtitle_style,
                )
            )
            offset_s += duration_s

        return Timeline(
            project_id=project.id,
            fps=fps,
            width=width,
            height=height,
            aspect_ratio=project.aspect_ratio,
            scenes=timeline_scenes,
        )

    # --- helpers ------------------------------------------------------------ #

    @staticmethod
    def _latest_by_scene(assets: list[Asset], *, kind: str) -> dict[int, Asset]:
        """Último asset (por id) de un tipo dado, indexado por ``scene_id``."""
        out: dict[int, Asset] = {}
        for asset in sorted(assets, key=lambda a: a.id):
            if asset.kind == kind and asset.scene_id is not None:
                out[asset.scene_id] = asset
        return out

    @staticmethod
    def _image_path(asset: Asset | None) -> str | None:
        return TimelineBuilder._asset_path(asset)

    @staticmethod
    def _asset_path(asset: Asset | None) -> str | None:
        if asset is None:
            return None
        return asset.path

    @staticmethod
    def _scene_duration(scene: Scene, voice_asset: Asset | None) -> float:
        if voice_asset is not None:
            voice_duration = float((voice_asset.meta or {}).get("duration_s", 0.0))
            if voice_duration > 0:
                return voice_duration
        if scene.duration_s and scene.duration_s > 0:
            return float(scene.duration_s)
        return MIN_SCENE_DURATION_S

    @staticmethod
    def _project_cues(assets: list[Asset]) -> list[Cue]:
        srt = next(
            (a for a in sorted(assets, key=lambda a: a.id, reverse=True) if a.kind == "srt"),
            None,
        )
        if srt is None:
            return []
        return _parse_srt_cues(srt.path)

    @staticmethod
    def _cues_for_window(
        project_cues: list[Cue], start_s: float, duration_s: float
    ) -> list[SubtitleCue]:
        """Selecciona los cues que caen en ``[start_s, start_s+duration_s)`` y los reubica.

        Los tiempos del ``.srt`` del proyecto son globales (acumulados por escena en el
        motor de voz); aquí se desplazan a coordenadas locales de la escena y se recortan
        a su ventana para que Remotion los muestre en el instante correcto.
        """
        end_s = start_s + duration_s
        local: list[SubtitleCue] = []
        for cue in project_cues:
            # El cue solapa la ventana de la escena.
            if cue.t1 <= start_s or cue.t0 >= end_s:
                continue
            local_start = max(0.0, cue.t0 - start_s)
            local_end = min(duration_s, cue.t1 - start_s)
            if local_end <= local_start:
                continue
            local.append(
                SubtitleCue(
                    start_s=round(local_start, 3),
                    end_s=round(local_end, 3),
                    text=cue.text,
                )
            )
        return local
