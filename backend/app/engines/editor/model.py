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
    "TextDecoration",
    "SubtitlePreset",
    "SubtitleStyle",
    "SubtitleWord",
    "TitleAnimation",
    "TitleStyle",
    "OverlayStyle",
    "EditClip",
    "EditTrack",
    "EditMeta",
    "EditProject",
    "VISUAL_KINDS",
    "VISUAL_RENDER_KINDS",
    "AUDIO_KINDS",
]

# Marca que distingue el documento de edición del timeline de generación en la tabla
# ``Timeline`` reutilizada (campo ``json['kind']``).
EDIT_PROJECT_KIND = "edit_project"

TrackKind = Literal[
    "video", "image", "voice", "music", "sfx", "subtitle", "title", "overlay"
]

#: Pistas de medio visual (clips con ``asset_id`` que pinta Remotino como <Img>/<Video>).
VISUAL_KINDS: tuple[TrackKind, ...] = ("video", "image")
#: Pistas que pinta la composición ``Edit`` por encima del fondo (visuales + título + overlay).
VISUAL_RENDER_KINDS: tuple[TrackKind, ...] = ("video", "image", "title", "overlay")
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


#: Presets de subtítulo admitidos (datos en ``presets.py``; ver §6 del diseño).
SubtitlePreset = Literal["classic", "tiktok", "reels", "shorts", "cinema", "clean"]


class TextDecoration(BaseModel):
    """Mixin de decoración fina de texto (contorno/sombra/fondo) para subtítulos/títulos.

    Todos los campos tienen default NEUTRO: un ``SubtitleStyle`` sin estos campos valida
    igual y, con los defaults, el render conserva EXACTAMENTE el aspecto previo (el
    ``outline_width=0`` deja el ``textShadow`` heredado de Classic/TikTok; ``shadow=False``
    y ``bg_opacity=1.0`` no alteran la caja por defecto). Se compone en ``SubtitleStyle``.
    """

    # Contorno fino (WebkitTextStroke). 0 = usar el textShadow heredado (compat).
    outline_color: str = "#000000"
    outline_width: float = 0.0
    # Sombra (textShadow).
    shadow: bool = False
    shadow_color: str = "#000000"
    shadow_blur: float = 0.0
    shadow_x: float = 0.0
    shadow_y: float = 0.0
    # Fondo (caja): opacidad sobre bg_color, padding (em) y radio (px).
    bg_opacity: float = 1.0
    bg_padding: float = 0.2
    bg_radius: float = 8.0

    @field_validator("outline_width", "shadow_blur", "bg_padding", "bg_radius")
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError(
                "outline_width/shadow_blur/bg_padding/bg_radius no pueden ser negativos"
            )
        return value

    @field_validator("bg_opacity")
    @classmethod
    def _opacity_range(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError("bg_opacity debe estar entre 0 y 1")
        return value


class SubtitleStyle(TextDecoration):
    """Estilo de un clip de subtítulo (tipografía/posición/preset + decoración fina)."""

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
    preset: SubtitlePreset = "classic"
    # Karaoke (resaltado de la palabra pronunciada). Defaults neutros: un documento
    # previo sin estos campos valida igual y produce el mismo render (sin karaoke).
    karaoke: bool = False
    highlight_color: str = "#FFE34D"
    highlight_scale: float = 1.0

    @field_validator("font_size")
    @classmethod
    def _font_size_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("style.font_size debe ser > 0")
        return value

    @field_validator("highlight_scale")
    @classmethod
    def _highlight_scale_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("style.highlight_scale debe ser > 0")
        return value


class SubtitleWord(BaseModel):
    """Una palabra con tiempos RELATIVOS al inicio del clip de subtítulo (karaoke).

    Se rellena desde ``palabras.json`` (``[{text,startMs,endMs}]``) al convertir el
    timeline (ver ``convert.py``). Lista vacía ⇒ sin karaoke por palabra.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    start_s: float = 0.0
    end_s: float = 0.0

    @field_validator("start_s", "end_s")
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("word.start_s/end_s no pueden ser negativos")
        return value


class TitleAnimation(BaseModel):
    """Animación de entrada/salida de un clip de título (intro/créditos/lower-third).

    Defaults neutros y retrocompatibles: un clip de título sin animación explícita usa
    ``fade`` ``both`` 0.8s. ``credits_roll`` desplaza el texto a lo largo de TODO el clip.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "none", "fade", "slide_up", "slide_down", "typewriter", "scale_in", "credits_roll"
    ] = "fade"
    duration_s: float = 0.8
    direction: Literal["in", "out", "both"] = "both"

    @field_validator("duration_s")
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("title_animation.duration_s no puede ser negativa")
        return value


class TitleStyle(TextDecoration):
    """Estilo de un clip de título (tipografía/posición/tipo + decoración fina heredada).

    Reutiliza el mixin ``TextDecoration`` (contorno/sombra/fondo) de los subtítulos para no
    duplicar validación. La posición es por anclaje en fracción del frame (0..1), pensada
    para el drag&drop del preview. ``kind`` selecciona el layout (``lower_third`` se
    preposiciona en la banda inferior).
    """

    model_config = ConfigDict(extra="forbid")

    font_family: str = "Inter, sans-serif"
    font_size: int = 96
    color: str = "#FFFFFF"
    bold: bool = True
    align: Literal["left", "center", "right"] = "center"
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    kind: Literal["intro", "lower_third", "credits", "plain"] = "plain"

    @field_validator("font_size")
    @classmethod
    def _font_size_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("title.font_size debe ser > 0")
        return value

    @field_validator("anchor_x", "anchor_y")
    @classmethod
    def _anchor_range(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError("title.anchor_x/anchor_y deben estar entre 0 y 1")
        return value


class OverlayStyle(BaseModel):
    """Estilo de un clip de overlay (imagen/logo/marca de agua/emoji gráfico).

    La imagen se dibuja a ``width_frac`` del ancho del frame (alto por aspecto del asset),
    anclada en ``anchor_x/anchor_y`` (fracción del frame, para drag&drop), con ``opacity``
    y ``rotation``. ``tile=True`` repite la imagen en mosaico (marca de agua).
    """

    model_config = ConfigDict(extra="forbid")

    opacity: float = 1.0
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    width_frac: float = 0.2
    rotation: float = 0.0
    tile: bool = False

    @field_validator("opacity", "anchor_x", "anchor_y", "width_frac")
    @classmethod
    def _frac_range(cls, value: float) -> float:
        if not (0.0 <= value <= 1.0):
            raise ValueError(
                "overlay.opacity/anchor_x/anchor_y/width_frac deben estar entre 0 y 1"
            )
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
    words: list[SubtitleWord] = Field(default_factory=list)

    # Title (texto animado: intro/lower-third/créditos). Reutiliza ``text`` como contenido.
    title: TitleStyle | None = None
    title_animation: TitleAnimation | None = None

    # Overlay (imagen/logo/marca de agua). Reutiliza ``asset_id`` como imagen.
    overlay: OverlayStyle | None = None

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
    # Zonas seguras verticales (fracción 0..1 del alto) para formatos 9:16: márgenes
    # superior/inferior que la UI de la red social puede tapar. Defaults NEUTROS (0.0):
    # un EditProject 16:9 previo valida igual y NO emite safeTop/safeBottom (regresión).
    safe_top_frac: float = 0.0
    safe_bottom_frac: float = 0.0

    @field_validator("fps", "width", "height")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("meta.fps/width/height deben ser > 0")
        return value

    @field_validator("safe_top_frac", "safe_bottom_frac")
    @classmethod
    def _safe_frac_range(cls, value: float) -> float:
        if not (0.0 <= value < 1.0):
            raise ValueError("meta.safe_*_frac debe estar en [0, 1)")
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
