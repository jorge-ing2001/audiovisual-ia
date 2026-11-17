"""Sugerencias deterministas sobre el estado del proyecto/``EditProject`` (FEAT-006, §3.2).

Funciones PURAS (sin ffmpeg, sin disco, sin render) que inspeccionan el estado y devuelven
avisos accionables con el MISMO shape que los checks del QA report
(``{id, label, status, detail}``). Al no tocar I/O son 100% testeables (tablas de estado
-> avisos esperados). El router (``GET /projects/{id}/suggestions``) solo lee de la BD / el
``EditProject`` activo y delega aquí.

Avisos cubiertos (§3.2):

- ``scene_without_image``: una escena de generación sin ningún ``Asset(kind='image')``.
- ``subtitle_overflow``: un cue de subtítulo cuyo ``end_s`` excede la duración de su
  escena/clip de referencia.
- ``track_overlap``: dos clips que se solapan en una pista que no debería solaparse
  (p. ej. voz, vídeo, subtítulos).
- (Opcional) ``scene_bad_duration``: una escena con ``duration_s <= 0``.
- (Opcional) ``project_without_voice``: el ``EditProject`` no tiene ninguna pista de voz
  con clips.

Severidad (``status``): se usan los mismos valores que los checks del QA
(``warn``/``fail``); una sugerencia nunca es ``pass`` (solo se emite cuando hay algo que
avisar). El orden de salida es estable (por tipo y luego por identificador).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "suggestion",
    "suggest_scenes_without_image",
    "suggest_subtitle_overflow",
    "suggest_track_overlaps",
    "suggest_scene_bad_duration",
    "suggest_project_without_voice",
    "build_suggestions",
]

# Pistas cuyos clips NO deberían solaparse entre sí (una sola "capa" temporal). Las pistas
# de medio que sí admiten solape (overlay, title) quedan fuera a propósito.
_NON_OVERLAPPING_KINDS = ("video", "image", "voice", "music", "sfx", "subtitle")

# Margen tolerado al comparar tiempos (evita falsos positivos por redondeo de floats).
_EPS = 1e-3


def suggestion(
    suggestion_id: str,
    label: str,
    status: str,
    detail: str,
    **extra: Any,
) -> dict:
    """Construye un aviso con el shape estándar de los checks del QA.

    ``extra`` permite adjuntar contexto (``scene_id``, ``clip_id``, ``track_id``...) sin
    romper el shape base ``{id, label, status, detail}``.
    """
    item: dict = {"id": suggestion_id, "label": label, "status": status, "detail": detail}
    item.update(extra)
    return item


# --------------------------------------------------------------------------- #
# Sugerencias puras
# --------------------------------------------------------------------------- #


def suggest_scenes_without_image(scenes: list[dict]) -> list[dict]:
    """Escenas sin ningún ``Asset(kind='image')``.

    ``scenes`` es una lista de dicts ``{id, order, has_image}`` (datos ya resueltos en el
    router). Emite un aviso ``fail`` por cada escena con ``has_image`` falso.
    """
    out: list[dict] = []
    for scene in sorted(scenes, key=lambda s: (s.get("order", 0), s.get("id", 0))):
        if scene.get("has_image"):
            continue
        scene_id = scene.get("id")
        order = scene.get("order")
        pos = f" (orden {order})" if order is not None else ""
        out.append(
            suggestion(
                f"scene_without_image:{scene_id}",
                "Escena sin imagen",
                "fail",
                f"La escena {scene_id}{pos} no tiene ninguna imagen asociada; "
                "genera o sube una antes de renderizar.",
                scene_id=scene_id,
            )
        )
    return out


def suggest_subtitle_overflow(
    cues: list[dict], reference_duration_s: float | None
) -> list[dict]:
    """Cues de subtítulo cuyo ``end_s`` excede la duración de referencia.

    ``cues`` es una lista de dicts ``{id, start_s, end_s}`` (tiempos absolutos de la línea
    de tiempo) y ``reference_duration_s`` la duración de la escena/clip de referencia (p.
    ej. la duración total del ``EditProject`` o de la escena). Emite un aviso ``warn`` por
    cada cue que termina más allá de ``reference_duration_s``. Sin referencia (``None``),
    no emite nada (no se puede juzgar el exceso).
    """
    if reference_duration_s is None:
        return []
    out: list[dict] = []
    for cue in sorted(cues, key=lambda c: (c.get("start_s", 0.0), str(c.get("id")))):
        end_s = float(cue.get("end_s", 0.0))
        if end_s <= reference_duration_s + _EPS:
            continue
        cue_id = cue.get("id")
        overflow = round(end_s - reference_duration_s, 3)
        out.append(
            suggestion(
                f"subtitle_overflow:{cue_id}",
                "Subtítulo fuera de duración",
                "warn",
                f"El subtítulo {cue_id} termina en {end_s:.2f}s, "
                f"{overflow:.2f}s más allá de la duración ({reference_duration_s:.2f}s).",
                clip_id=cue_id,
            )
        )
    return out


def suggest_track_overlaps(tracks: list[dict]) -> list[dict]:
    """Clips que se solapan en una pista que no debería solaparse.

    ``tracks`` es una lista de dicts ``{id, kind, clips:[{id, start_s, duration_s}]}``.
    Para cada pista de un ``kind`` no solapable, ordena sus clips por ``start_s`` y emite un
    aviso ``warn`` por cada par consecutivo que se solape (``start siguiente < fin
    anterior``). No considera clips deshabilitados si traen ``enabled=False``.
    """
    out: list[dict] = []
    for track in tracks:
        if track.get("kind") not in _NON_OVERLAPPING_KINDS:
            continue
        clips = [
            c for c in track.get("clips", []) if c.get("enabled", True)
        ]
        ordered = sorted(clips, key=lambda c: float(c.get("start_s", 0.0)))
        for prev, cur in zip(ordered, ordered[1:]):
            prev_end = float(prev.get("start_s", 0.0)) + float(prev.get("duration_s", 0.0))
            cur_start = float(cur.get("start_s", 0.0))
            overlap = prev_end - cur_start
            if overlap > _EPS:
                out.append(
                    suggestion(
                        f"track_overlap:{track.get('id')}:{prev.get('id')}:{cur.get('id')}",
                        "Clips solapados",
                        "warn",
                        f"En la pista {track.get('id')!r} los clips {prev.get('id')!r} y "
                        f"{cur.get('id')!r} se solapan {overlap:.2f}s.",
                        track_id=track.get("id"),
                        clip_id=cur.get("id"),
                    )
                )
    return out


def suggest_scene_bad_duration(scenes: list[dict]) -> list[dict]:
    """Escenas con ``duration_s <= 0`` (duración sospechosa)."""
    out: list[dict] = []
    for scene in sorted(scenes, key=lambda s: (s.get("order", 0), s.get("id", 0))):
        duration = scene.get("duration_s")
        if duration is None or float(duration) > 0:
            continue
        scene_id = scene.get("id")
        out.append(
            suggestion(
                f"scene_bad_duration:{scene_id}",
                "Escena con duración inválida",
                "fail",
                f"La escena {scene_id} tiene una duración de {float(duration):.2f}s "
                "(debe ser > 0).",
                scene_id=scene_id,
            )
        )
    return out


def suggest_project_without_voice(tracks: list[dict]) -> list[dict]:
    """Un único aviso ``warn`` si no hay ninguna pista de voz con clips habilitados."""
    has_voice = any(
        track.get("kind") == "voice"
        and any(c.get("enabled", True) for c in track.get("clips", []))
        for track in tracks
    )
    if has_voice:
        return []
    return [
        suggestion(
            "project_without_voice",
            "Proyecto sin voz",
            "warn",
            "El proyecto no tiene ninguna pista de voz con clips; el vídeo saldrá sin "
            "narración.",
        )
    ]


def build_suggestions(
    scenes: list[dict],
    tracks: list[dict],
    reference_duration_s: float | None,
    *,
    subtitle_cues: list[dict] | None = None,
) -> list[dict]:
    """Agrega todas las sugerencias puras en una lista estable.

    ``scenes`` y ``tracks`` son los datos ya resueltos por el router; ``subtitle_cues`` son
    los cues de subtítulo a validar contra ``reference_duration_s`` (si no se pasan, se
    derivan de las pistas ``subtitle`` de ``tracks``). El orden es determinista: escenas sin
    imagen, duraciones inválidas, subtítulos fuera de duración, solapes y, por último, el
    aviso global de voz.
    """
    if subtitle_cues is None:
        subtitle_cues = [
            {"id": c.get("id"), "start_s": c.get("start_s", 0.0), "end_s": (
                float(c.get("start_s", 0.0)) + float(c.get("duration_s", 0.0))
            )}
            for track in tracks
            if track.get("kind") == "subtitle"
            for c in track.get("clips", [])
            if c.get("enabled", True)
        ]

    out: list[dict] = []
    out.extend(suggest_scenes_without_image(scenes))
    out.extend(suggest_scene_bad_duration(scenes))
    out.extend(suggest_subtitle_overflow(subtitle_cues, reference_duration_s))
    out.extend(suggest_track_overlaps(tracks))
    out.extend(suggest_project_without_voice(tracks))
    return out
