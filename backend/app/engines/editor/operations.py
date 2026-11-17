"""Operaciones de edición PURAS: ``op(edit_project, params) -> edit_project``.

Cada operación toma un ``EditProject`` y unos ``params`` (dict) y devuelve un NUEVO
``EditProject`` (inmutable): no muta el de entrada ni toca disco/render. Esto permite
tests deterministas sin ffmpeg/Chromium. Las invariantes (sin duraciones negativas,
tiempos dentro del origen recortable, clip/pista existentes) se validan y, si no se
pueden cumplir, se lanza ``EditOperationError`` con un mensaje accionable.

Operaciones del MVP (ver ``docs/EDITOR-MULTITRACK-ARCHITECTURE.md`` §3.2 y
``docs/EDITOR-MVP-SCOPE.md`` §2):
- Clips: ``trim``, ``split``, ``merge``, ``delete``, ``duplicate``, ``move``,
  ``set_duration``.
- Audio: ``set_clip_gain`` y ``set_track_volume`` (volumen por clip/pista), ``set_fade``
  (fade in/out), y ``move`` reposiciona voces/música/SFX.
- Visual: ``replace_asset`` (otro Asset del proyecto), ``set_transform``
  (escala/posición/encuadre/fit) y ``set_transition`` (none|fade entre escenas).
- Subtítulos: ``set_text``, ``set_timing``, ``set_style`` (tipografía/tamaño/color/fondo/
  negrita/contorno/posición/preset) y ``split``/``merge``.

Despacho por nombre: ``apply_operation(edit_project, op, params)``.
"""

from __future__ import annotations

from collections.abc import Callable

from app.engines.editor.model import (
    AUDIO_KINDS,
    VISUAL_KINDS,
    ClipTransition,
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
    OverlayStyle,
    SubtitleStyle,
    TitleAnimation,
    TitleStyle,
    VisualTransform,
)
from app.engines.editor.presets import (
    FORMAT_PRESETS,
    SUBTITLE_PRESETS,
    subtitle_style_from_preset,
)

__all__ = [
    "EditOperationError",
    "apply_operation",
    "OPERATIONS",
    "op_trim",
    "op_split",
    "op_delete",
    "op_duplicate",
    "op_move",
    "op_set_duration",
    "op_set_clip_gain",
    "op_set_track_volume",
    "op_set_fade",
    "op_replace_asset",
    "op_set_transform",
    "op_set_transition",
    "op_set_text",
    "op_set_timing",
    "op_set_style",
    "op_set_karaoke",
    "op_apply_subtitle_preset",
    "op_apply_subtitle_preset_all",
    "op_set_format",
    "op_merge",
    "op_add_track",
    "op_add_title",
    "op_set_title_style",
    "op_set_title_animation",
    "op_add_overlay",
    "op_set_overlay_style",
    "op_set_position",
    "op_set_track_flags",
    "op_ripple_delete",
]

#: Tipos de pista válidos para ``op_add_track`` (enum completo de ``TrackKind``).
_TRACK_KINDS = ("video", "image", "voice", "music", "sfx", "subtitle", "title", "overlay")

# Presets de subtítulo admitidos (validación del campo ``preset``).
_SUBTITLE_PRESETS = tuple(sorted(SUBTITLE_PRESETS))


class EditOperationError(ValueError):
    """Una operación de edición no se puede aplicar (invariante o referencia inválida)."""


def _clone(edit_project: EditProject) -> EditProject:
    """Copia profunda validada del proyecto (base inmutable para cada operación)."""
    return edit_project.model_copy(deep=True)


def _require_clip(edit_project: EditProject, clip_id: str) -> EditClip:
    found = edit_project.find_clip(clip_id)
    if found is None:
        raise EditOperationError(f"No existe el clip {clip_id!r} en el proyecto de edición.")
    return found[1]


def _require_str(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise EditOperationError(f"El parámetro {key!r} (str) es obligatorio.")
    return value


def _require_track(edit_project: EditProject, track_id: str):
    track = edit_project.track_by_id(track_id)
    if track is None:
        raise EditOperationError(f"No existe la pista {track_id!r} en el proyecto de edición.")
    return track


def _require_unlocked(track: EditTrack) -> EditTrack:
    """Rechaza mutar los clips de una pista bloqueada (``locked``).

    Se invoca en todas las operaciones que modifican clips (trim/split/merge/delete/
    ripple/move/duplicate/duración/gain/fade/transform/transición/texto/timing/estilo/
    karaoke/título/overlay/posición...). ``locked`` es protección de edición: NO afecta
    al preview ni al export (ver ``EditTrack``).
    """
    if track.locked:
        raise EditOperationError(
            f"La pista {track.id!r} está bloqueada (locked); desbloquéala para editar sus "
            "clips."
        )
    return track


def _require_clip_unlocked(
    edit_project: EditProject, clip_id: str
) -> tuple[EditTrack, EditClip]:
    """Localiza ``(track, clip)`` por id y rechaza si su pista está bloqueada."""
    found = edit_project.find_clip(clip_id)
    if found is None:
        raise EditOperationError(f"No existe el clip {clip_id!r} en el proyecto de edición.")
    track, clip = found
    _require_unlocked(track)
    return track, clip


def _require_number(params: dict, key: str) -> float:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EditOperationError(f"El parámetro {key!r} (número) es obligatorio.")
    return float(value)


def _source_span(clip: EditClip) -> float | None:
    """Longitud disponible del origen recortable, o ``None`` si no hay límite conocido."""
    if clip.source_out_s is None:
        return None
    return clip.source_out_s - clip.source_in_s


# --------------------------------------------------------------------------- #
# Clips (visual y genérico)
# --------------------------------------------------------------------------- #


def op_trim(edit_project: EditProject, params: dict) -> EditProject:
    """Recorta un clip ajustando ``source_in_s``/``source_out_s`` y ``duration_s``.

    Params: ``clip_id`` y, opcionales, ``source_in_s`` y ``source_out_s`` (absolutos en
    el origen). La nueva duración en la línea de tiempo pasa a ser ``source_out - in``.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))

    new_in = float(params.get("source_in_s", clip.source_in_s))
    new_out = params.get("source_out_s", clip.source_out_s)
    new_out = None if new_out is None else float(new_out)

    if new_in < 0:
        raise EditOperationError("trim: source_in_s no puede ser negativo.")
    if new_out is not None and new_out <= new_in:
        raise EditOperationError("trim: source_out_s debe ser mayor que source_in_s.")

    clip.source_in_s = round(new_in, 3)
    clip.source_out_s = None if new_out is None else round(new_out, 3)
    if new_out is not None:
        clip.duration_s = round(new_out - new_in, 3)
    if clip.duration_s <= 0:
        raise EditOperationError("trim: la duración resultante debe ser > 0.")
    return result


def op_split(edit_project: EditProject, params: dict) -> EditProject:
    """Parte un clip en dos en el instante absoluto ``t_s`` de la línea de tiempo.

    Params: ``clip_id`` y ``t_s`` (debe caer estrictamente dentro del clip). El segundo
    clip conserva asset/estilo y continúa el recorte del origen.
    """
    result = _clone(edit_project)
    clip_id = _require_str(params, "clip_id")
    t_s = _require_number(params, "t_s")
    found = result.find_clip(clip_id)
    if found is None:
        raise EditOperationError(f"split: no existe el clip {clip_id!r}.")
    track, clip = found
    _require_unlocked(track)

    if not (clip.start_s < t_s < clip.end_s):
        raise EditOperationError(
            f"split: t_s={t_s} debe caer dentro del clip ({clip.start_s}..{clip.end_s})."
        )

    left_duration = round(t_s - clip.start_s, 3)
    right_duration = round(clip.end_s - t_s, 3)
    if left_duration <= 0 or right_duration <= 0:
        raise EditOperationError("split: ambos fragmentos deben durar > 0.")

    second = clip.model_copy(deep=True)
    second.id = f"{clip.id}_b"
    second.start_s = round(t_s, 3)
    second.duration_s = right_duration

    # Reparte el recorte del origen proporcionalmente al punto de corte.
    clip.duration_s = left_duration
    if clip.source_out_s is not None:
        cut_source = round(clip.source_in_s + left_duration, 3)
        second.source_in_s = cut_source
        clip.source_out_s = cut_source

    index = track.clips.index(clip)
    track.clips.insert(index + 1, second)
    return result


def op_delete(edit_project: EditProject, params: dict) -> EditProject:
    """Elimina un clip por ``clip_id``."""
    result = _clone(edit_project)
    clip_id = _require_str(params, "clip_id")
    found = result.find_clip(clip_id)
    if found is None:
        raise EditOperationError(f"delete: no existe el clip {clip_id!r}.")
    track, clip = found
    _require_unlocked(track)
    track.clips.remove(clip)
    return result


def op_duplicate(edit_project: EditProject, params: dict) -> EditProject:
    """Duplica un clip; la copia arranca justo al final del original (misma pista)."""
    result = _clone(edit_project)
    clip_id = _require_str(params, "clip_id")
    found = result.find_clip(clip_id)
    if found is None:
        raise EditOperationError(f"duplicate: no existe el clip {clip_id!r}.")
    track, clip = found
    _require_unlocked(track)

    copy_clip = clip.model_copy(deep=True)
    copy_clip.id = f"{clip.id}_copy"
    copy_clip.start_s = round(clip.end_s, 3)
    index = track.clips.index(clip)
    track.clips.insert(index + 1, copy_clip)
    return result


def op_move(edit_project: EditProject, params: dict) -> EditProject:
    """Reposiciona un clip en el tiempo (``start_s``) y/o lo mueve a otra pista.

    Params: ``clip_id``, opcional ``start_s`` (>= 0) y opcional ``track_id`` (pista del
    MISMO ``kind``). Sirve también para reposicionar voces (audio).
    """
    result = _clone(edit_project)
    clip_id = _require_str(params, "clip_id")
    found = result.find_clip(clip_id)
    if found is None:
        raise EditOperationError(f"move: no existe el clip {clip_id!r}.")
    track, clip = found
    _require_unlocked(track)

    if "start_s" in params:
        new_start = float(params["start_s"])
        if new_start < 0:
            raise EditOperationError("move: start_s no puede ser negativo.")
        clip.start_s = round(new_start, 3)

    target_track_id = params.get("track_id")
    if target_track_id and target_track_id != track.id:
        target = result.track_by_id(target_track_id)
        if target is None:
            raise EditOperationError(f"move: la pista {target_track_id!r} no existe.")
        _require_unlocked(target)
        if target.kind != clip.kind:
            raise EditOperationError(
                f"move: no se puede mover un clip {clip.kind!r} a una pista {target.kind!r}."
            )
        track.clips.remove(clip)
        clip.track_id = target.id
        target.clips.append(clip)
    return result


def op_set_duration(edit_project: EditProject, params: dict) -> EditProject:
    """Fija la duración de un clip en la línea de tiempo (``duration_s`` > 0).

    Para clips recortables respeta el origen: no se puede exceder ``source_out - in``.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    duration = _require_number(params, "duration_s")
    if duration <= 0:
        raise EditOperationError("set_duration: la duración debe ser > 0.")

    span = _source_span(clip)
    if span is not None and duration > span + 1e-6:
        raise EditOperationError(
            f"set_duration: {duration}s excede el origen recortado ({round(span, 3)}s)."
        )
    clip.duration_s = round(duration, 3)
    if clip.source_out_s is not None:
        clip.source_out_s = round(clip.source_in_s + clip.duration_s, 3)
    return result


# --------------------------------------------------------------------------- #
# Audio
# --------------------------------------------------------------------------- #


def op_set_clip_gain(edit_project: EditProject, params: dict) -> EditProject:
    """Fija el volumen (``gain`` >= 0) de un clip de audio (voice/music/sfx)."""
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind not in AUDIO_KINDS:
        raise EditOperationError(
            f"set_clip_gain: el clip {clip.id!r} no es de audio ({clip.kind!r})."
        )
    gain = _require_number(params, "gain")
    if gain < 0:
        raise EditOperationError("set_clip_gain: gain no puede ser negativo.")
    clip.gain = float(gain)
    return result


def op_set_track_volume(edit_project: EditProject, params: dict) -> EditProject:
    """Fija el volumen (``volume`` >= 0) de una PISTA de audio (voice/music/sfx).

    Params: ``track_id`` y ``volume`` (>= 0). El volumen de pista se multiplica por el
    ``gain`` de cada clip al compilar (export) y en el preview, de modo que subir/bajar
    una pista afecta a toda su música/voz/SFX por igual.
    """
    result = _clone(edit_project)
    track = _require_track(result, _require_str(params, "track_id"))
    if track.kind not in AUDIO_KINDS:
        raise EditOperationError(
            f"set_track_volume: la pista {track.id!r} no es de audio ({track.kind!r})."
        )
    volume = _require_number(params, "volume")
    if volume < 0:
        raise EditOperationError("set_track_volume: volume no puede ser negativo.")
    track.volume = float(volume)
    return result


def op_set_fade(edit_project: EditProject, params: dict) -> EditProject:
    """Fija los fades de un clip de audio (``fade_in_s`` y/o ``fade_out_s`` >= 0).

    Params: ``clip_id`` y, opcionales, ``fade_in_s`` y ``fade_out_s``. La suma de ambos
    no puede exceder la duración del clip (no tendría sentido un fade más largo que el
    propio clip). Se reflejan como filtros ``afade`` en la preparación del ``MixTrack``.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind not in AUDIO_KINDS:
        raise EditOperationError(
            f"set_fade: el clip {clip.id!r} no es de audio ({clip.kind!r})."
        )

    fade_in = clip.fade_in_s
    fade_out = clip.fade_out_s
    if "fade_in_s" in params:
        fade_in = _require_number(params, "fade_in_s")
    if "fade_out_s" in params:
        fade_out = _require_number(params, "fade_out_s")
    if fade_in < 0 or fade_out < 0:
        raise EditOperationError("set_fade: los fades no pueden ser negativos.")
    if fade_in + fade_out > clip.duration_s + 1e-6:
        raise EditOperationError(
            f"set_fade: fade_in+fade_out ({round(fade_in + fade_out, 3)}s) "
            f"excede la duración del clip ({clip.duration_s}s)."
        )
    clip.fade_in_s = round(float(fade_in), 3)
    clip.fade_out_s = round(float(fade_out), 3)
    return result


# --------------------------------------------------------------------------- #
# Visual (reemplazo de recurso, transform y transiciones)
# --------------------------------------------------------------------------- #


def op_replace_asset(edit_project: EditProject, params: dict) -> EditProject:
    """Reemplaza el recurso (``asset_id``) de un clip visual por otro ``Asset``.

    Params: ``clip_id`` y ``asset_id`` (int). Al cambiar de recurso se resetea el recorte
    del origen (``source_in_s=0``, ``source_out_s=None``) porque el nuevo recurso tiene su
    propia extensión; la duración en la línea de tiempo se conserva.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind not in VISUAL_KINDS:
        raise EditOperationError(
            f"replace_asset: el clip {clip.id!r} no es visual ({clip.kind!r})."
        )
    asset_id = params.get("asset_id")
    if isinstance(asset_id, bool) or not isinstance(asset_id, int):
        raise EditOperationError("replace_asset: 'asset_id' (int) es obligatorio.")
    clip.asset_id = int(asset_id)
    clip.source_in_s = 0.0
    clip.source_out_s = None
    return result


def op_set_transform(edit_project: EditProject, params: dict) -> EditProject:
    """Ajusta el transform (encuadre) de un clip visual.

    Params: ``clip_id`` y cualquiera de ``scale`` (>0), ``x``, ``y``, ``rotation``,
    ``crop_l/crop_t/crop_r/crop_b`` (0..1) y ``fit`` (``cover``|``contain``). Los campos
    no indicados conservan su valor actual.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind not in VISUAL_KINDS:
        raise EditOperationError(
            f"set_transform: el clip {clip.id!r} no es visual ({clip.kind!r})."
        )

    base = (clip.transform or VisualTransform()).model_dump()
    for key in ("scale", "x", "y", "rotation", "crop_l", "crop_t", "crop_r", "crop_b"):
        if key in params:
            base[key] = _require_number(params, key)
    if "fit" in params:
        fit = params.get("fit")
        if fit not in ("cover", "contain"):
            raise EditOperationError("set_transform: 'fit' debe ser 'cover' o 'contain'.")
        base["fit"] = fit
    for key in ("crop_l", "crop_t", "crop_r", "crop_b"):
        if not (0.0 <= base[key] <= 1.0):
            raise EditOperationError(f"set_transform: '{key}' debe estar entre 0 y 1.")
    try:
        clip.transform = VisualTransform.model_validate(base)
    except Exception as exc:  # pydantic ValidationError -> mensaje accionable
        raise EditOperationError(f"set_transform: {exc}") from exc
    return result


def op_set_transition(edit_project: EditProject, params: dict) -> EditProject:
    """Fija la transición de entrada/salida de un clip visual.

    Params: ``clip_id``, ``edge`` (``in``|``out``, por defecto ``in``), ``type``
    (``none``|``fade``) y ``duration_s`` (>= 0). ``none`` quita la transición.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind not in VISUAL_KINDS:
        raise EditOperationError(
            f"set_transition: el clip {clip.id!r} no es visual ({clip.kind!r})."
        )
    edge = params.get("edge", "in")
    if edge not in ("in", "out"):
        raise EditOperationError("set_transition: 'edge' debe ser 'in' o 'out'.")
    t_type = params.get("type", "fade")
    if t_type not in ("none", "fade"):
        raise EditOperationError("set_transition: 'type' debe ser 'none' o 'fade' en el MVP.")
    duration = float(params.get("duration_s", 0.5))
    if duration < 0:
        raise EditOperationError("set_transition: duration_s no puede ser negativa.")

    transition = ClipTransition(
        type=t_type, duration_s=0.0 if t_type == "none" else round(duration, 3)
    )
    if edge == "in":
        clip.transition_in = transition
    else:
        clip.transition_out = transition
    return result


# --------------------------------------------------------------------------- #
# Subtítulos
# --------------------------------------------------------------------------- #


def op_set_text(edit_project: EditProject, params: dict) -> EditProject:
    """Cambia el texto de un clip de subtítulo."""
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind != "subtitle":
        raise EditOperationError(
            f"set_text: el clip {clip.id!r} no es un subtítulo ({clip.kind!r})."
        )
    text = params.get("text")
    if not isinstance(text, str):
        raise EditOperationError("set_text: 'text' (str) es obligatorio.")
    clip.text = text
    return result


def op_set_timing(edit_project: EditProject, params: dict) -> EditProject:
    """Fija ``start_s`` (>= 0) y/o ``duration_s`` (> 0) de un clip de subtítulo."""
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind != "subtitle":
        raise EditOperationError(
            f"set_timing: el clip {clip.id!r} no es un subtítulo ({clip.kind!r})."
        )
    if "start_s" in params:
        start = float(params["start_s"])
        if start < 0:
            raise EditOperationError("set_timing: start_s no puede ser negativo.")
        clip.start_s = round(start, 3)
    if "duration_s" in params:
        duration = float(params["duration_s"])
        if duration <= 0:
            raise EditOperationError("set_timing: duration_s debe ser > 0.")
        clip.duration_s = round(duration, 3)
    return result


# Campos numéricos del estilo (tipografía + decoración fina de TextDecoration).
_STYLE_NUMERIC = {
    "font_size",
    "x",
    "y",
    "outline_width",
    "shadow_blur",
    "shadow_x",
    "shadow_y",
    "bg_opacity",
    "bg_padding",
    "bg_radius",
    "highlight_scale",
}
_STYLE_STR = {"font_family", "color", "outline_color", "shadow_color", "highlight_color"}
_STYLE_BOOL = {"bold", "outline", "shadow", "karaoke"}


def op_set_style(edit_project: EditProject, params: dict) -> EditProject:
    """Fija el estilo completo de un clip de subtítulo (incluida la decoración fina).

    Params: ``clip_id`` y cualquiera de ``font_family`` (str), ``font_size`` (int > 0),
    ``color`` (str), ``bg_color`` (str|null), ``bold`` (bool), ``outline`` (bool),
    ``position`` (``top``|``center``|``bottom``), ``x``/``y`` (px), ``preset``
    (``classic``|``tiktok``|``reels``|``shorts``|``cinema``|``clean``) y los campos de
    contorno/sombra/fondo (``outline_color``, ``outline_width``, ``shadow``,
    ``shadow_color``, ``shadow_blur``, ``shadow_x``, ``shadow_y``, ``bg_opacity``,
    ``bg_padding``, ``bg_radius``). Los campos no indicados conservan su valor.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind != "subtitle":
        raise EditOperationError(
            f"set_style: el clip {clip.id!r} no es un subtítulo ({clip.kind!r})."
        )

    base = (clip.style or SubtitleStyle()).model_dump()
    for key in _STYLE_NUMERIC:
        if key in params:
            value = params[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EditOperationError(f"set_style: '{key}' debe ser numérico.")
            base[key] = int(value) if key == "font_size" else float(value)
    for key in _STYLE_STR:
        if key in params:
            value = params[key]
            if not isinstance(value, str) or not value:
                raise EditOperationError(f"set_style: '{key}' (str) no puede ser vacío.")
            base[key] = value
    for key in _STYLE_BOOL:
        if key in params:
            if not isinstance(params[key], bool):
                raise EditOperationError(f"set_style: '{key}' debe ser booleano.")
            base[key] = params[key]
    if "bg_color" in params:
        value = params["bg_color"]
        if value is not None and not isinstance(value, str):
            raise EditOperationError("set_style: 'bg_color' debe ser str o null.")
        base["bg_color"] = value
    if "position" in params:
        if params["position"] not in ("top", "center", "bottom"):
            raise EditOperationError("set_style: 'position' inválida.")
        base["position"] = params["position"]
    if "preset" in params:
        if params["preset"] not in _SUBTITLE_PRESETS:
            raise EditOperationError(
                f"set_style: 'preset' debe ser uno de {', '.join(_SUBTITLE_PRESETS)}."
            )
        base["preset"] = params["preset"]
    try:
        clip.style = SubtitleStyle.model_validate(base)
    except Exception as exc:
        raise EditOperationError(f"set_style: {exc}") from exc
    return result


def _apply_preset_to_clip(clip: EditClip, preset: str) -> None:
    """Aplica un preset de ``SUBTITLE_PRESETS`` a un clip de subtítulo (muta el clip).

    Preserva la posición manual (``x``/``y``) y el estado de karaoke del clip, que son
    decisiones del usuario independientes del preset estético.
    """
    try:
        style = subtitle_style_from_preset(preset)
    except ValueError as exc:
        raise EditOperationError(f"apply_subtitle_preset: {exc}") from exc
    if clip.style is not None:
        style = style.model_copy(
            update={
                "x": clip.style.x,
                "y": clip.style.y,
                "karaoke": clip.style.karaoke,
                "highlight_color": clip.style.highlight_color,
                "highlight_scale": clip.style.highlight_scale,
            }
        )
    clip.style = style


def op_apply_subtitle_preset(edit_project: EditProject, params: dict) -> EditProject:
    """Aplica un preset de subtítulo (set de valores de estilo) a un clip.

    Params: ``clip_id`` y ``preset`` (uno de ``SUBTITLE_PRESETS``). Conserva la posición
    manual y el karaoke del clip.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind != "subtitle":
        raise EditOperationError(
            f"apply_subtitle_preset: el clip {clip.id!r} no es un subtítulo ({clip.kind!r})."
        )
    preset = _require_str(params, "preset")
    _apply_preset_to_clip(clip, preset)
    return result


def op_apply_subtitle_preset_all(edit_project: EditProject, params: dict) -> EditProject:
    """Aplica un preset a TODOS los clips de subtítulo (opcionalmente de una pista).

    Params: ``preset`` (uno de ``SUBTITLE_PRESETS``) y, opcional, ``track_id`` (si se
    indica, solo esa pista; si no, todas las pistas de subtítulo). No falla si no hay
    clips de subtítulo (no-op).
    """
    result = _clone(edit_project)
    preset = _require_str(params, "preset")
    if preset not in SUBTITLE_PRESETS:
        raise EditOperationError(
            f"apply_subtitle_preset_all: preset desconocido {preset!r}."
        )
    track_id = params.get("track_id")
    if track_id is not None and not isinstance(track_id, str):
        raise EditOperationError("apply_subtitle_preset_all: 'track_id' debe ser str.")
    if track_id is not None and result.track_by_id(track_id) is None:
        raise EditOperationError(
            f"apply_subtitle_preset_all: la pista {track_id!r} no existe."
        )
    for track in result.tracks:
        if track.kind != "subtitle":
            continue
        if track_id is not None and track.id != track_id:
            continue
        # Pistas bloqueadas se saltan (protección de edición); no abortan el gesto global.
        if track.locked:
            continue
        for clip in track.clips:
            _apply_preset_to_clip(clip, preset)
    return result


def op_set_format(edit_project: EditProject, params: dict) -> EditProject:
    """Fija el formato del proyecto (resolución + zonas seguras) en ``meta``.

    Params: o bien ``format`` (clave de ``FORMAT_PRESETS``: ``16:9``/``1:1``/``tiktok``/
    ``reels``/``shorts``), o bien valores sueltos ``width``/``height``/``aspect_ratio`` y
    opcionalmente ``safe_top_frac``/``safe_bottom_frac``. Un preset de formato fija también
    las zonas seguras (9:16 ⇒ márgenes; 16:9/1:1 ⇒ 0). Ajusta ``meta`` sin tocar clips.
    """
    result = _clone(edit_project)
    base = result.meta.model_dump()

    fmt = params.get("format")
    if fmt is not None:
        if fmt not in FORMAT_PRESETS:
            raise EditOperationError(
                f"set_format: formato desconocido {fmt!r}. "
                f"Disponibles: {', '.join(sorted(FORMAT_PRESETS))}."
            )
        preset = FORMAT_PRESETS[fmt]
        base.update(
            {
                "width": preset["width"],
                "height": preset["height"],
                "aspect_ratio": preset["aspect_ratio"],
                "safe_top_frac": preset["safe_top_frac"],
                "safe_bottom_frac": preset["safe_bottom_frac"],
            }
        )

    for key in ("width", "height"):
        if key in params:
            value = params[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise EditOperationError(f"set_format: '{key}' debe ser un entero > 0.")
            base[key] = int(value)
    if "aspect_ratio" in params:
        value = params["aspect_ratio"]
        if not isinstance(value, str) or not value:
            raise EditOperationError("set_format: 'aspect_ratio' (str) no puede ser vacío.")
        base["aspect_ratio"] = value
    for key in ("safe_top_frac", "safe_bottom_frac"):
        if key in params:
            value = params[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EditOperationError(f"set_format: '{key}' debe ser numérico.")
            base[key] = float(value)

    try:
        result.meta = EditMeta.model_validate(base)
    except Exception as exc:
        raise EditOperationError(f"set_format: {exc}") from exc
    return result


def op_set_karaoke(edit_project: EditProject, params: dict) -> EditProject:
    """Activa/configura el karaoke (resaltado por palabra) de un clip de subtítulo.

    Params: ``clip_id``, ``enabled`` (bool) y, opcionales, ``highlight_color`` (str) y
    ``highlight_scale`` (número > 0). El resaltado por palabra usa el ``words`` del clip
    (rellenado desde ``palabras.json`` al convertir); si el clip no tiene palabras, el
    render cae al resaltado por reparto uniforme. Rechaza clips no-subtítulo.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind != "subtitle":
        raise EditOperationError(
            f"set_karaoke: el clip {clip.id!r} no es un subtítulo ({clip.kind!r})."
        )
    enabled = params.get("enabled")
    if not isinstance(enabled, bool):
        raise EditOperationError("set_karaoke: 'enabled' (bool) es obligatorio.")

    base = (clip.style or SubtitleStyle()).model_dump()
    base["karaoke"] = enabled
    if "highlight_color" in params:
        value = params["highlight_color"]
        if not isinstance(value, str) or not value:
            raise EditOperationError("set_karaoke: 'highlight_color' (str) no puede ser vacío.")
        base["highlight_color"] = value
    if "highlight_scale" in params:
        value = params["highlight_scale"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EditOperationError("set_karaoke: 'highlight_scale' debe ser numérico.")
        if value <= 0:
            raise EditOperationError("set_karaoke: 'highlight_scale' debe ser > 0.")
        base["highlight_scale"] = float(value)
    try:
        clip.style = SubtitleStyle.model_validate(base)
    except Exception as exc:
        raise EditOperationError(f"set_karaoke: {exc}") from exc
    return result


def op_merge(edit_project: EditProject, params: dict) -> EditProject:
    """Une dos clips ADYACENTES de la misma pista en uno solo (inverso de ``split``).

    Params: ``clip_id`` (el primero) y ``other_id`` (el siguiente). El clip resultante
    abarca de ``start_s`` del primero al fin del segundo; para subtítulos se concatena el
    texto (primero + ' ' + segundo) y para clips recortables se une el rango del origen.
    """
    result = _clone(edit_project)
    clip_id = _require_str(params, "clip_id")
    other_id = _require_str(params, "other_id")
    if clip_id == other_id:
        raise EditOperationError("merge: clip_id y other_id deben ser distintos.")

    found_a = result.find_clip(clip_id)
    found_b = result.find_clip(other_id)
    if found_a is None:
        raise EditOperationError(f"merge: no existe el clip {clip_id!r}.")
    if found_b is None:
        raise EditOperationError(f"merge: no existe el clip {other_id!r}.")
    track_a, clip_a = found_a
    track_b, clip_b = found_b
    if track_a.id != track_b.id:
        raise EditOperationError("merge: los clips deben estar en la misma pista.")
    _require_unlocked(track_a)

    first, second = (clip_a, clip_b) if clip_a.start_s <= clip_b.start_s else (clip_b, clip_a)
    if first.kind != second.kind:
        raise EditOperationError("merge: no se pueden unir clips de distinto tipo.")
    if second.start_s > first.end_s + 1e-3:
        raise EditOperationError(
            "merge: los clips no son adyacentes (hay un hueco entre ellos)."
        )

    first.duration_s = round(second.end_s - first.start_s, 3)
    if first.kind == "subtitle":
        left = (first.text or "").strip()
        right = (second.text or "").strip()
        first.text = f"{left} {right}".strip()
    elif first.source_out_s is not None and second.source_out_s is not None:
        first.source_out_s = round(first.source_in_s + first.duration_s, 3)

    track_a.clips.remove(second)
    return result


# --------------------------------------------------------------------------- #
# Pistas (crear pista de cualquier tipo, p. ej. título/overlay)
# --------------------------------------------------------------------------- #


def op_add_track(edit_project: EditProject, params: dict) -> EditProject:
    """Crea una PISTA nueva de un ``kind`` dado (p. ej. ``title`` u ``overlay``).

    Params: ``kind`` (uno de ``TrackKind``), ``name`` (str, opcional) y ``order`` (int,
    opcional; por defecto al final). Devuelve el proyecto con la pista añadida. El ``id``
    de la pista se deriva de forma determinista (``track_<kind>`` o con sufijo si colisiona)
    para que el frontend pueda localizarla; se admite ``track_id`` explícito.
    """
    result = _clone(edit_project)
    kind = _require_str(params, "kind")
    if kind not in _TRACK_KINDS:
        raise EditOperationError(
            f"add_track: 'kind' debe ser uno de {', '.join(_TRACK_KINDS)}."
        )
    name = params.get("name")
    if name is not None and not isinstance(name, str):
        raise EditOperationError("add_track: 'name' debe ser str.")

    order = params.get("order")
    if order is None:
        order = max((t.order for t in result.tracks), default=-1) + 1
    elif isinstance(order, bool) or not isinstance(order, (int, float)):
        raise EditOperationError("add_track: 'order' debe ser numérico.")
    else:
        order = int(order)

    track_id = params.get("track_id")
    if track_id is not None and not isinstance(track_id, str):
        raise EditOperationError("add_track: 'track_id' debe ser str.")
    if not track_id:
        track_id = f"track_{kind}"
        if result.track_by_id(track_id) is not None:
            n = 2
            while result.track_by_id(f"{track_id}_{n}") is not None:
                n += 1
            track_id = f"{track_id}_{n}"
    elif result.track_by_id(track_id) is not None:
        raise EditOperationError(f"add_track: la pista {track_id!r} ya existe.")

    track = EditTrack(id=track_id, kind=kind, name=name or "", order=order)
    result.tracks.append(track)
    return result


# --------------------------------------------------------------------------- #
# Títulos (texto animado: intro / lower-third / créditos)
# --------------------------------------------------------------------------- #


def op_add_title(edit_project: EditProject, params: dict) -> EditProject:
    """Crea un clip de título en una pista ``title``.

    Params: ``track_id`` (pista existente de kind ``title``), ``text`` (str no vacío),
    ``start_s`` (>= 0), ``duration_s`` (> 0) y, opcionales, ``title`` (dict de ``TitleStyle``)
    y ``animation`` (dict de ``TitleAnimation``). Si no se pasa ``title``/``animation`` se
    usan los defaults del modelo. El ``id`` del clip es determinista salvo colisión.
    """
    result = _clone(edit_project)
    track = _require_track(result, _require_str(params, "track_id"))
    _require_unlocked(track)
    if track.kind != "title":
        raise EditOperationError(
            f"add_title: la pista {track.id!r} no es de títulos ({track.kind!r})."
        )
    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        raise EditOperationError("add_title: 'text' (str no vacío) es obligatorio.")
    start_s = _require_number(params, "start_s")
    duration_s = _require_number(params, "duration_s")
    if start_s < 0:
        raise EditOperationError("add_title: start_s no puede ser negativo.")
    if duration_s <= 0:
        raise EditOperationError("add_title: duration_s debe ser > 0.")

    try:
        title = TitleStyle.model_validate(params.get("title") or {})
    except Exception as exc:
        raise EditOperationError(f"add_title: título inválido: {exc}") from exc
    try:
        animation = TitleAnimation.model_validate(params.get("animation") or {})
    except Exception as exc:
        raise EditOperationError(f"add_title: animación inválida: {exc}") from exc

    base_id = params.get("clip_id")
    if base_id is not None and not isinstance(base_id, str):
        raise EditOperationError("add_title: 'clip_id' debe ser str.")
    clip_id = base_id or f"title_{len(track.clips)}"
    if result.find_clip(clip_id) is not None:
        n = len(track.clips) + 1
        while result.find_clip(f"title_{n}") is not None:
            n += 1
        clip_id = f"title_{n}"

    clip = EditClip(
        id=clip_id,
        track_id=track.id,
        kind="title",
        start_s=round(start_s, 3),
        duration_s=round(duration_s, 3),
        text=text,
        title=title,
        title_animation=animation,
    )
    track.clips.append(clip)
    return result


# Campos del TitleStyle por tipo (reutiliza la decoración fina de TextDecoration).
_TITLE_NUMERIC = {
    "font_size",
    "anchor_x",
    "anchor_y",
    "outline_width",
    "shadow_blur",
    "shadow_x",
    "shadow_y",
    "bg_opacity",
    "bg_padding",
    "bg_radius",
}
_TITLE_STR = {"font_family", "color", "outline_color", "shadow_color"}
_TITLE_BOOL = {"bold", "shadow"}


def op_set_title_style(edit_project: EditProject, params: dict) -> EditProject:
    """Edita el estilo de un clip de título (campos de ``TitleStyle``).

    Params: ``clip_id`` y cualquiera de ``font_family``, ``font_size`` (int > 0),
    ``color``, ``bold`` (bool), ``align`` (``left``|``center``|``right``), ``anchor_x``/
    ``anchor_y`` (0..1), ``kind`` (``intro``|``lower_third``|``credits``|``plain``) y los
    campos de contorno/sombra/fondo heredados. Los no indicados conservan su valor.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind != "title":
        raise EditOperationError(
            f"set_title_style: el clip {clip.id!r} no es un título ({clip.kind!r})."
        )

    base = (clip.title or TitleStyle()).model_dump()
    for key in _TITLE_NUMERIC:
        if key in params:
            value = params[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EditOperationError(f"set_title_style: '{key}' debe ser numérico.")
            base[key] = int(value) if key == "font_size" else float(value)
    for key in _TITLE_STR:
        if key in params:
            value = params[key]
            if not isinstance(value, str) or not value:
                raise EditOperationError(f"set_title_style: '{key}' (str) no puede ser vacío.")
            base[key] = value
    for key in _TITLE_BOOL:
        if key in params:
            if not isinstance(params[key], bool):
                raise EditOperationError(f"set_title_style: '{key}' debe ser booleano.")
            base[key] = params[key]
    if "align" in params:
        if params["align"] not in ("left", "center", "right"):
            raise EditOperationError("set_title_style: 'align' inválido.")
        base["align"] = params["align"]
    if "kind" in params:
        if params["kind"] not in ("intro", "lower_third", "credits", "plain"):
            raise EditOperationError("set_title_style: 'kind' inválido.")
        base["kind"] = params["kind"]
    try:
        clip.title = TitleStyle.model_validate(base)
    except Exception as exc:
        raise EditOperationError(f"set_title_style: {exc}") from exc
    return result


def op_set_title_animation(edit_project: EditProject, params: dict) -> EditProject:
    """Edita la animación de un clip de título (campos de ``TitleAnimation``).

    Params: ``clip_id`` y cualquiera de ``type`` (enum de ``TitleAnimation``),
    ``duration_s`` (>= 0) y ``direction`` (``in``|``out``|``both``). Los no indicados
    conservan su valor.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind != "title":
        raise EditOperationError(
            f"set_title_animation: el clip {clip.id!r} no es un título ({clip.kind!r})."
        )

    base = (clip.title_animation or TitleAnimation()).model_dump()
    if "type" in params:
        base["type"] = params["type"]
    if "duration_s" in params:
        value = params["duration_s"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EditOperationError("set_title_animation: 'duration_s' debe ser numérico.")
        if value < 0:
            raise EditOperationError("set_title_animation: 'duration_s' no puede ser negativa.")
        base["duration_s"] = float(value)
    if "direction" in params:
        base["direction"] = params["direction"]
    try:
        clip.title_animation = TitleAnimation.model_validate(base)
    except Exception as exc:
        raise EditOperationError(f"set_title_animation: {exc}") from exc
    return result


# --------------------------------------------------------------------------- #
# Overlays (imagen / logo / marca de agua / emoji gráfico)
# --------------------------------------------------------------------------- #


def op_add_overlay(edit_project: EditProject, params: dict) -> EditProject:
    """Crea un clip de overlay (imagen) en una pista ``overlay``.

    Params: ``track_id`` (pista existente de kind ``overlay``), ``asset_id`` (int,
    imagen subida), ``start_s`` (>= 0), ``duration_s`` (> 0) y, opcional, ``overlay``
    (dict de ``OverlayStyle``). Si no se pasa ``overlay`` se usan los defaults del modelo.
    """
    result = _clone(edit_project)
    track = _require_track(result, _require_str(params, "track_id"))
    _require_unlocked(track)
    if track.kind != "overlay":
        raise EditOperationError(
            f"add_overlay: la pista {track.id!r} no es de overlays ({track.kind!r})."
        )
    asset_id = params.get("asset_id")
    if isinstance(asset_id, bool) or not isinstance(asset_id, int):
        raise EditOperationError("add_overlay: 'asset_id' (int) es obligatorio.")
    start_s = _require_number(params, "start_s")
    duration_s = _require_number(params, "duration_s")
    if start_s < 0:
        raise EditOperationError("add_overlay: start_s no puede ser negativo.")
    if duration_s <= 0:
        raise EditOperationError("add_overlay: duration_s debe ser > 0.")

    try:
        overlay = OverlayStyle.model_validate(params.get("overlay") or {})
    except Exception as exc:
        raise EditOperationError(f"add_overlay: overlay inválido: {exc}") from exc

    base_id = params.get("clip_id")
    if base_id is not None and not isinstance(base_id, str):
        raise EditOperationError("add_overlay: 'clip_id' debe ser str.")
    clip_id = base_id or f"overlay_{len(track.clips)}"
    if result.find_clip(clip_id) is not None:
        n = len(track.clips) + 1
        while result.find_clip(f"overlay_{n}") is not None:
            n += 1
        clip_id = f"overlay_{n}"

    clip = EditClip(
        id=clip_id,
        track_id=track.id,
        kind="overlay",
        start_s=round(start_s, 3),
        duration_s=round(duration_s, 3),
        asset_id=int(asset_id),
        overlay=overlay,
    )
    track.clips.append(clip)
    return result


_OVERLAY_NUMERIC = {"opacity", "anchor_x", "anchor_y", "width_frac", "rotation"}


def op_set_overlay_style(edit_project: EditProject, params: dict) -> EditProject:
    """Edita el estilo de un clip de overlay (campos de ``OverlayStyle``).

    Params: ``clip_id`` y cualquiera de ``opacity``/``anchor_x``/``anchor_y``/``width_frac``
    (0..1), ``rotation`` (grados) y ``tile`` (bool). Los no indicados conservan su valor.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))
    if clip.kind != "overlay":
        raise EditOperationError(
            f"set_overlay_style: el clip {clip.id!r} no es un overlay ({clip.kind!r})."
        )

    base = (clip.overlay or OverlayStyle()).model_dump()
    for key in _OVERLAY_NUMERIC:
        if key in params:
            value = params[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EditOperationError(f"set_overlay_style: '{key}' debe ser numérico.")
            base[key] = float(value)
    if "tile" in params:
        if not isinstance(params["tile"], bool):
            raise EditOperationError("set_overlay_style: 'tile' debe ser booleano.")
        base["tile"] = params["tile"]
    try:
        clip.overlay = OverlayStyle.model_validate(base)
    except Exception as exc:
        raise EditOperationError(f"set_overlay_style: {exc}") from exc
    return result


# --------------------------------------------------------------------------- #
# Posición (drag&drop sobre el preview: ancla para título/overlay; x/y px para subtítulo)
# --------------------------------------------------------------------------- #


def op_set_position(edit_project: EditProject, params: dict) -> EditProject:
    """Reposiciona un clip a partir del arrastre sobre el preview (FEAT-008).

    Es la operación pura que persiste UNA vez por gesto de drag&drop (al soltar). El
    mapeo de coordenadas del Player a estos parámetros lo hace la función pura del
    frontend ``dragToAnchor``; aquí solo validamos y escribimos en el modelo:

    - ``title``/``overlay``: fija el anclaje ``anchor_x``/``anchor_y`` (fracción del frame,
      0..1) de ``TitleStyle``/``OverlayStyle``. Params: ``clip_id`` y ``anchor_x`` y/o
      ``anchor_y``. Conserva el resto del estilo. Si el estilo no existe, parte del default.
    - ``subtitle``: fija el desplazamiento ``x``/``y`` en PÍXELES de ``SubtitleStyle``
      (relativo a la posición del preset). Params: ``clip_id`` y ``x`` y/o ``y``.

    No admite otros tipos de clip (video/image usan ``set_transform``; audio no se
    posiciona). Lanza ``EditOperationError`` con mensaje accionable si faltan parámetros,
    si el tipo no admite posicionado o si el ancla cae fuera de 0..1.
    """
    result = _clone(edit_project)
    _, clip = _require_clip_unlocked(result, _require_str(params, "clip_id"))

    if clip.kind in ("title", "overlay"):
        has_x = "anchor_x" in params
        has_y = "anchor_y" in params
        if not has_x and not has_y:
            raise EditOperationError(
                "set_position: se requiere 'anchor_x' y/o 'anchor_y' (0..1) para "
                f"clips {clip.kind!r}."
            )
        if clip.kind == "title":
            base = (clip.title or TitleStyle()).model_dump()
        else:
            base = (clip.overlay or OverlayStyle()).model_dump()
        if has_x:
            base["anchor_x"] = _require_number(params, "anchor_x")
        if has_y:
            base["anchor_y"] = _require_number(params, "anchor_y")
        for key in ("anchor_x", "anchor_y"):
            if not (0.0 <= base[key] <= 1.0):
                raise EditOperationError(
                    f"set_position: '{key}' debe estar entre 0 y 1 (recibido {base[key]})."
                )
        try:
            if clip.kind == "title":
                clip.title = TitleStyle.model_validate(base)
            else:
                clip.overlay = OverlayStyle.model_validate(base)
        except Exception as exc:
            raise EditOperationError(f"set_position: {exc}") from exc
    elif clip.kind == "subtitle":
        has_x = "x" in params
        has_y = "y" in params
        if not has_x and not has_y:
            raise EditOperationError(
                "set_position: se requiere 'x' y/o 'y' (px) para clips de subtítulo."
            )
        base = (clip.style or SubtitleStyle()).model_dump()
        if has_x:
            base["x"] = _require_number(params, "x")
        if has_y:
            base["y"] = _require_number(params, "y")
        try:
            clip.style = SubtitleStyle.model_validate(base)
        except Exception as exc:
            raise EditOperationError(f"set_position: {exc}") from exc
    else:
        raise EditOperationError(
            f"set_position: el clip {clip.id!r} es {clip.kind!r}; solo se posicionan "
            "title/overlay (ancla) y subtitle (x/y). Usa set_transform para video/image."
        )
    return result


# --------------------------------------------------------------------------- #
# Propiedades de pista (bloquear / ocultar / silenciar) y edición ripple
# --------------------------------------------------------------------------- #


def op_set_track_flags(edit_project: EditProject, params: dict) -> EditProject:
    """Fija una o varias banderas de una pista (``locked``/``hidden``/``muted``).

    Params: ``track_id`` y cualquiera de ``locked``/``hidden``/``muted`` (bool). Al menos
    una debe venir. Semántica (ver ``EditTrack``): ``muted`` silencia AUDIO, ``hidden``
    oculta VISUAL en preview/export, ``locked`` protege la edición. La operación no rechaza
    poner ``muted`` en una pista visual ni ``hidden`` en una de audio: es un no-op semántico
    fuera de su dominio (el compilador solo consume cada bandera en las pistas donde aplica).
    Una sola operación cubre lock/hide/mute (menos superficie que tres).
    """
    result = _clone(edit_project)
    track = _require_track(result, _require_str(params, "track_id"))

    touched = False
    for key in ("locked", "hidden", "muted"):
        if key in params:
            value = params[key]
            if not isinstance(value, bool):
                raise EditOperationError(
                    f"set_track_flags: '{key}' debe ser booleano."
                )
            setattr(track, key, value)
            touched = True
    if not touched:
        raise EditOperationError(
            "set_track_flags: indica al menos una bandera ('locked'/'hidden'/'muted')."
        )
    return result


def op_ripple_delete(edit_project: EditProject, params: dict) -> EditProject:
    """Elimina un clip y cierra el hueco desplazando a la izquierda los posteriores.

    Params: ``clip_id``. Borra el clip y desplaza hacia la izquierda, por la duración del
    clip borrado, todos los clips de LA MISMA pista que empiezan en o después del inicio
    del clip eliminado (``start_s >= start_s`` del borrado), de modo que no quede hueco. No
    toca otras pistas (ripple por pista). Rechaza si la pista está ``locked``. Ningún
    ``start_s`` resultante baja de 0 (el desplazamiento solo afecta a clips posteriores).
    """
    result = _clone(edit_project)
    clip_id = _require_str(params, "clip_id")
    found = result.find_clip(clip_id)
    if found is None:
        raise EditOperationError(f"ripple_delete: no existe el clip {clip_id!r}.")
    track, clip = found
    _require_unlocked(track)

    gap = clip.duration_s
    deleted_start = clip.start_s
    track.clips.remove(clip)
    for other in track.clips:
        if other.start_s >= deleted_start - 1e-9:
            other.start_s = round(max(0.0, other.start_s - gap), 3)
    return result


OPERATIONS: dict[str, Callable[[EditProject, dict], EditProject]] = {
    "trim": op_trim,
    "split": op_split,
    "merge": op_merge,
    "delete": op_delete,
    "duplicate": op_duplicate,
    "move": op_move,
    "set_duration": op_set_duration,
    "set_clip_gain": op_set_clip_gain,
    "set_track_volume": op_set_track_volume,
    "set_fade": op_set_fade,
    "replace_asset": op_replace_asset,
    "set_transform": op_set_transform,
    "set_transition": op_set_transition,
    "set_text": op_set_text,
    "set_timing": op_set_timing,
    "set_style": op_set_style,
    "set_karaoke": op_set_karaoke,
    "apply_subtitle_preset": op_apply_subtitle_preset,
    "apply_subtitle_preset_all": op_apply_subtitle_preset_all,
    "set_format": op_set_format,
    "add_track": op_add_track,
    "add_title": op_add_title,
    "set_title_style": op_set_title_style,
    "set_title_animation": op_set_title_animation,
    "add_overlay": op_add_overlay,
    "set_overlay_style": op_set_overlay_style,
    "set_position": op_set_position,
    "set_track_flags": op_set_track_flags,
    "ripple_delete": op_ripple_delete,
}


def apply_operation(edit_project: EditProject, op: str, params: dict | None) -> EditProject:
    """Aplica la operación pura ``op`` con ``params`` y devuelve el nuevo ``EditProject``.

    Lanza ``EditOperationError`` si la operación no existe o sus invariantes fallan.
    """
    func = OPERATIONS.get(op)
    if func is None:
        raise EditOperationError(
            f"Operación de edición desconocida: {op!r}. "
            f"Disponibles: {', '.join(sorted(OPERATIONS))}."
        )
    return func(edit_project, dict(params or {}))
