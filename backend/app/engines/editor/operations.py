"""Operaciones de edición PURAS: ``op(edit_project, params) -> edit_project``.

Cada operación toma un ``EditProject`` y unos ``params`` (dict) y devuelve un NUEVO
``EditProject`` (inmutable): no muta el de entrada ni toca disco/render. Esto permite
tests deterministas sin ffmpeg/Chromium. Las invariantes (sin duraciones negativas,
tiempos dentro del origen recortable, clip/pista existentes) se validan y, si no se
pueden cumplir, se lanza ``EditOperationError`` con un mensaje accionable.

Slice mínimo (ver ``docs/EDITOR-MULTITRACK-ARCHITECTURE.md`` §3.2):
- Clips: ``trim``, ``split``, ``delete``, ``duplicate``, ``move``, ``set_duration``.
- Audio: ``set_clip_gain`` (y ``move`` sirve para reposicionar voces).
- Subtítulos: ``set_text``, ``set_timing``.

Despacho por nombre: ``apply_operation(edit_project, op, params)``.
"""

from __future__ import annotations

from collections.abc import Callable

from app.engines.editor.model import AUDIO_KINDS, EditClip, EditProject

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
    "op_set_text",
    "op_set_timing",
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


OPERATIONS: dict[str, Callable[[EditProject, dict], EditProject]] = {
    "trim": op_trim,
    "split": op_split,
    "delete": op_delete,
    "duplicate": op_duplicate,
    "move": op_move,
    "set_duration": op_set_duration,
    "set_clip_gain": op_set_clip_gain,
    "set_text": op_set_text,
    "set_timing": op_set_timing,
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
