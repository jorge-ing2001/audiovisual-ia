"""Modelo de datos del editor multipista (``EditProject``).

Documento JSON versionado y validado con Pydantic que describe un proyecto de edición
de vídeo MULTIPISTA (vídeo/imagen, voz, música, SFX, subtítulos como pistas
independientes), independiente del ``Timeline`` por escena de generación. Vive en la
tabla ``Timeline`` reutilizada (marcado con ``kind='edit_project'``) y se historiza en
``Version`` para undo/redo. Ver ``docs/EDITOR-MULTITRACK-ARCHITECTURE.md`` §3.1.

Todo clip tiene tiempos ABSOLUTOS en la línea de tiempo (``start_s``, ``duration_s``) y,
cuando referencia un recurso recortable, el sub-rango del origen (``source_in_s``,
``source_out_s``). Esto hace que split/trim/reposición sean operaciones puras sobre
números, testeables sin render (ver ``operations.py``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "EDIT_PROJECT_KIND",
    "TrackKind",
    "VisualTransform",
    "ClipTransition",
    "ClipEffect",
    "SubtitleStyle",
    "EditClip",
    "EditTrack",
    "EditMeta",
    "EditProject",
    "VISUAL_KINDS",
    "AUDIO_KINDS",
]

# Marca que distingue el documento de edición del timeline de generación en la tabla
# ``Timeline`` reutilizada (campo ``json['kind']``).
EDIT_PROJECT_KIND = "edit_project"

TrackKind = Literal["video", "image", "voice", "music", "sfx", "subtitle"]

#: Pistas cuyo clip pinta Remotion (composición ``Edit``).
VISUAL_KINDS: tuple[TrackKind, ...] = ("video", "image")
#: Pistas que alimentan el ``AudioMixer`` (voice/music/sfx).
AUDIO_KINDS: tuple[TrackKind, ...] = ("voice", "music", "sfx")


class VisualTransform(BaseModel):
    """Transformación de un clip visual (encuadre/posición dentro del frame)."""

    model_config = ConfigDict(extra="forbid")

    scale: float = 1.0
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    crop_l: float = 0.0
    crop_t: float = 0.0
    crop_r: float = 0.0
    crop_b: float = 0.0
    fit: Literal["cover", "contain"] = "cover"

    @field_validator("scale")
    @classmethod
    def _scale_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("transform.scale debe ser > 0")
        return value


class ClipTransition(BaseModel):
    """Transición de entrada/salida de un clip visual."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["none", "fade", "slide", "wipe"] = "none"
    duration_s: float = 0.0

    @field_validator("duration_s")
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("transition.duration_s no puede ser negativa")
        return value


class ClipEffect(BaseModel):
    """Efecto/animación aplicado a un clip visual (p. ej. Ken Burns)."""

    model_config = ConfigDict(extra="forbid")

    type: str
    params: dict = Field(default_factory=dict)


class SubtitleStyle(BaseModel):
    """Estilo de un clip de subtítulo (tipografía/posición/preset)."""

    model_config = ConfigDict(extra="forbid")

    font_family: str = "Inter, sans-serif"
    font_size: int = 48
    color: str = "#FFFFFF"
    bg_color: str | None = None
    bold: bool = True
    outline: bool = True
    position: Literal["top", "center", "bottom"] = "bottom"
    x: float = 0.0
    y: float = 0.0
    preset: Literal["classic", "tiktok"] = "classic"

    @field_validator("font_size")
    @classmethod
    def _font_size_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("style.font_size debe ser > 0")
        return value


class EditClip(BaseModel):
    """Un clip posicionado en una pista del editor.

    Campos comunes a todos los tipos de pista más las extensiones por tipo
    (``transform``/``transition``/``effects`` para visual; ``gain``/fades para audio;
    ``text``/``style`` para subtítulo). Un clip es válido con ``duration_s > 0`` y, si
    recorta un origen, ``source_in_s <= source_out_s``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    track_id: str
    kind: TrackKind
    start_s: float = 0.0
    duration_s: float = 0.0
    source_in_s: float = 0.0
    source_out_s: float | None = None
    asset_id: int | None = None
    z_order: int = 0
    enabled: bool = True

    # Visual (video/image)
    transform: VisualTransform | None = None
    transition_in: ClipTransition | None = None
    transition_out: ClipTransition | None = None
    effects: list[ClipEffect] = Field(default_factory=list)

    # Audio (voice/music/sfx)
    gain: float = 1.0
    fade_in_s: float = 0.0
    fade_out_s: float = 0.0

    # Subtitle
    text: str | None = None
    style: SubtitleStyle | None = None

    @field_validator("start_s")
    @classmethod
    def _start_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("clip.start_s no puede ser negativo")
        return value

    @field_validator("duration_s")
    @classmethod
    def _duration_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("clip.duration_s no puede ser negativa")
        return value

    @field_validator("source_in_s")
    @classmethod
    def _source_in_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("clip.source_in_s no puede ser negativo")
        return value

    @field_validator("gain", "fade_in_s", "fade_out_s")
    @classmethod
    def _audio_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("gain/fade no pueden ser negativos")
        return value

    @model_validator(mode="after")
    def _check_source_window(self) -> "EditClip":
        if self.source_out_s is not None and self.source_out_s < self.source_in_s:
            raise ValueError(
                "clip.source_out_s no puede ser menor que source_in_s "
                f"({self.source_out_s} < {self.source_in_s})"
            )
        return self

    @property
    def end_s(self) -> float:
        """Instante final del clip en la línea de tiempo."""
        return self.start_s + self.duration_s


class EditTrack(BaseModel):
    """Una pista del editor: colección ordenada de clips de un mismo tipo."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: TrackKind
    name: str = ""
    order: int = 0
    muted: bool = False
    locked: bool = False
    volume: float = 1.0
    clips: list[EditClip] = Field(default_factory=list)

    @field_validator("volume")
    @classmethod
    def _volume_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("track.volume no puede ser negativo")
        return value

    @model_validator(mode="after")
    def _clips_match_track(self) -> "EditTrack":
        for clip in self.clips:
            if clip.track_id != self.id:
                raise ValueError(
                    f"clip {clip.id!r} tiene track_id {clip.track_id!r} != {self.id!r}"
                )
            if clip.kind != self.kind:
                raise ValueError(
                    f"clip {clip.id!r} es {clip.kind!r} en pista {self.kind!r}"
                )
        return self


class EditMeta(BaseModel):
    """Metadatos globales del proyecto de edición (resolución/fps/aspecto)."""

    model_config = ConfigDict(extra="forbid")

    fps: int = 30
    width: int = 1920
    height: int = 1080
    aspect_ratio: str = "16:9"

    @field_validator("fps", "width", "height")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("meta.fps/width/height deben ser > 0")
        return value


class EditProject(BaseModel):
    """Documento de edición multipista versionado (contrato frontend ↔ backend)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["edit_project"] = EDIT_PROJECT_KIND
    project_id: int
    meta: EditMeta
    tracks: list[EditTrack] = Field(default_factory=list)
    source_timeline_version: int | None = None

    @property
    def duration_s(self) -> float:
        """Duración total (fin del clip más tardío de cualquier pista)."""
        end = 0.0
        for track in self.tracks:
            for clip in track.clips:
                if clip.enabled:
                    end = max(end, clip.end_s)
        return round(end, 3)

    def track_by_id(self, track_id: str) -> EditTrack | None:
        """Devuelve la pista con ese id (o ``None``)."""
        for track in self.tracks:
            if track.id == track_id:
                return track
        return None

    def find_clip(self, clip_id: str) -> tuple[EditTrack, EditClip] | None:
        """Localiza ``(track, clip)`` por id de clip (o ``None`` si no existe)."""
        for track in self.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return track, clip
        return None
