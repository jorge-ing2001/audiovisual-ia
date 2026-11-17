"""Orquestación del QA report: mide sobre el MP4 del render y evalúa (FEAT-004).

``build_qa_report(project, video_asset, session)`` mide (``measure.py``) sobre el fichero
del ``Asset(kind='video')`` o recoge ``skip_reason``, evalúa de forma pura (``checks.py``)
y arma el documento con TRAZABILIDAD completa (``docs/ADMIN-QUALITY.md`` §2.5):

- ``schema``/``qa_engine_version``/``generated_at`` (ISO 8601 UTC).
- ``project_id``/``asset_id``/``video_path``/``render_hash`` (sha256 del MP4).
- ``overall`` (regla conservadora), ``thresholds`` efectivos, ``checks[]`` y
  ``skipped_checks[]``.

Si el MP4 no existe o una medición falla, los controles afectados quedan ``skipped`` con
su ``skip_reason`` (nunca se inventa medición). El reporte se persiste en
``Asset.meta['qa_report']`` (ver ``persist_qa_report``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.engines.qa import QA_REPORT_SCHEMA, qa_engine_version
from app.engines.qa import checks as qa_checks
from app.engines.qa import measure as qa_measure
from app.models.entities import Asset, Project, Scene

__all__ = [
    "build_qa_report",
    "persist_qa_report",
    "expected_duration_s",
    "subtitle_cues",
]


def _iso_utc_now() -> str:
    """Timestamp ISO 8601 en UTC con sufijo ``Z`` (p. ej. ``2025-01-30T12:34:56Z``)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def expected_duration_s(project: Project, session: Session) -> float | None:
    """Duración esperada del render: EditProject activo si existe, si no suma de escenas.

    Prioriza el ``EditProject`` activo (el render del editor manda sobre la generación). Si
    no hay editor, usa la suma de ``Scene.duration_s``. ``None`` si no hay nada que comparar.
    """
    try:
        from app.engines.editor.store import load_active_edit_project

        edit_project = load_active_edit_project(project, session)
    except Exception:  # noqa: BLE001 - un editor corrupto no debe romper el QA
        edit_project = None
    if edit_project is not None:
        duration = edit_project.duration_s
        if duration and duration > 0:
            return float(duration)

    total = 0.0
    for scene in session.query(Scene).filter(Scene.project_id == project.id):
        total += float(scene.duration_s or 0.0)
    return total if total > 0 else None


def subtitle_cues(project: Project, session: Session) -> list[tuple[float, float]] | None:
    """Cues ``(start_s, end_s)`` de subtítulo del EditProject activo (pistas ``subtitle``).

    ``None`` si el proyecto no tiene subtítulos (nada que validar -> el check queda
    ``skipped`` con razón ``no_subtitles``).
    """
    try:
        from app.engines.editor.store import load_active_edit_project

        edit_project = load_active_edit_project(project, session)
    except Exception:  # noqa: BLE001
        edit_project = None
    if edit_project is None:
        return None
    cues: list[tuple[float, float]] = []
    for track in edit_project.tracks:
        if track.kind != "subtitle":
            continue
        for clip in track.clips:
            if not clip.enabled:
                continue
            cues.append((clip.start_s, clip.end_s))
    return cues or None


def build_qa_report(project: Project, video_asset: Asset, session: Session) -> dict:
    """Construye el ``qa_report`` del render midiendo sobre el MP4 del ``video_asset``.

    No persiste: devuelve el dict. Úsese con ``persist_qa_report`` para escribirlo en el
    ``Asset.meta``. Toda rama de fallo degrada a ``skipped`` con razón (no inventa nada).
    """
    thresholds = qa_checks.load_thresholds()
    video_path = video_asset.path
    path = Path(video_path) if video_path else None

    # --- Mediciones (I/O) --------------------------------------------------- #
    probe_result = qa_measure.measure_probe(video_path) if path else qa_measure.MeasureResult(
        skip_reason="no_video"
    )
    probe = probe_result.value if probe_result.skip_reason is None else None
    has_audio = probe.has_audio if probe is not None else None

    loudness_result = qa_measure.measure_loudness(video_path, has_audio=has_audio)
    black_result = qa_measure.measure_black_frames(
        video_path, black_min_s=thresholds["black_frames"]["black_min_s"]
    )

    actual_duration = probe.duration_s if probe is not None else None
    width = probe.width if probe is not None else None
    height = probe.height if probe is not None else None

    # --- Metadatos del proyecto -------------------------------------------- #
    expected_dur = expected_duration_s(project, session)
    cues = subtitle_cues(project, session)

    # --- Evaluación (pura) -------------------------------------------------- #
    check_list = [
        qa_checks.check_loudness(
            loudness_result.value if loudness_result.skip_reason is None else None,
            thresholds,
            skip_reason=loudness_result.skip_reason,
        ),
        qa_checks.check_black_frames(
            black_result.value if black_result.skip_reason is None else None,
            thresholds,
            skip_reason=black_result.skip_reason,
        ),
        qa_checks.check_duration_match(
            actual_duration,
            expected_dur,
            thresholds,
            skip_reason=probe_result.skip_reason,
        ),
        qa_checks.check_resolution_aspect(
            width,
            height,
            thresholds,
            expected_aspect=project.aspect_ratio,
            skip_reason=probe_result.skip_reason,
        ),
        qa_checks.check_subtitle_sync(
            cues,
            actual_duration,
            thresholds,
            # Si no hay MP4, no se conoce la duración -> skip no_video; si no hay cues,
            # check_subtitle_sync devuelve no_subtitles por sí mismo.
            skip_reason=probe_result.skip_reason if cues else None,
        ),
    ]

    overall = qa_checks.compute_overall(check_list)
    skipped_checks = [
        {"id": c["id"], "skip_reason": c.get("skip_reason")}
        for c in check_list
        if c["status"] == qa_checks.STATUS_SKIPPED
    ]

    render_hash = qa_measure.measure_render_hash(video_path) if path else None

    return {
        "schema": QA_REPORT_SCHEMA,
        "qa_engine_version": qa_engine_version,
        "project_id": project.id,
        "asset_id": video_asset.id,
        "video_path": video_path,
        "render_hash": render_hash,
        "generated_at": _iso_utc_now(),
        "overall": overall,
        "thresholds": thresholds,
        "checks": check_list,
        "skipped_checks": skipped_checks,
    }


def persist_qa_report(video_asset: Asset, report: dict) -> None:
    """Escribe el reporte en ``Asset.meta['qa_report']`` (sin crear tabla nueva).

    Reasigna ``meta`` a un dict nuevo para que SQLAlchemy detecte el cambio del campo JSON
    (mutar in-place no marca el atributo como sucio).
    """
    meta = dict(video_asset.meta or {})
    meta["qa_report"] = report
    video_asset.meta = meta
