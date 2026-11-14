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
    EditProject,
    SubtitleStyle,
    VisualTransform,
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
    "op_merge",
]


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
    clip = _require_clip(result, _require_str(params, "clip_id"))

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
    clip = _require_clip(result, _require_str(params, "clip_id"))
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
    clip = _require_clip(result, _require_str(params, "clip_id"))
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
    clip = _require_clip(result, _require_str(params, "clip_id"))
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
    clip = _require_clip(result, _require_str(params, "clip_id"))
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
    clip = _require_clip(result, _require_str(params, "clip_id"))
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
    clip = _require_clip(result, _require_str(params, "clip_id"))
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
    clip = _require_clip(result, _require_str(params, "clip_id"))
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
    clip = _require_clip(result, _require_str(params, "clip_id"))
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


_STYLE_NUMERIC = {"font_size", "x", "y"}
_STYLE_STR = {"font_family", "color"}
_STYLE_BOOL = {"bold", "outline"}


def op_set_style(edit_project: EditProject, params: dict) -> EditProject:
    """Fija el estilo completo de un clip de subtítulo.

    Params: ``clip_id`` y cualquiera de ``font_family`` (str), ``font_size`` (int > 0),
    ``color`` (str), ``bg_color`` (str|null), ``bold`` (bool), ``outline`` (bool),
    ``position`` (``top``|``center``|``bottom``), ``x``/``y`` (px) y ``preset``
    (``classic``|``tiktok``). Los campos no indicados conservan su valor.
    """
    result = _clone(edit_project)
    clip = _require_clip(result, _require_str(params, "clip_id"))
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
        if params["preset"] not in ("classic", "tiktok"):
            raise EditOperationError("set_style: 'preset' debe ser 'classic' o 'tiktok'.")
        base["preset"] = params["preset"]
    try:
        clip.style = SubtitleStyle.model_validate(base)
    except Exception as exc:
        raise EditOperationError(f"set_style: {exc}") from exc
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
