"""Router REST del editor multipista (motor ``editor``, FEAT-002).

Todas las rutas cuelgan del proyecto y operan sobre el ``EditProject`` activo, persistido
en la tabla ``Timeline`` reutilizada e historizado en ``Version`` (undo/redo). Ver
``docs/EDITOR-MULTITRACK-ARCHITECTURE.md`` §4.

- ``POST /projects/{id}/editor/open``   abre/deriva el editor (idempotente).
- ``GET  /projects/{id}/editor``         estado completo del ``EditProject`` activo.
- ``PUT  /projects/{id}/editor``         reemplaza el estado completo (+ Version).
- ``POST /projects/{id}/editor/op``      aplica UNA operación pura (+ Version).
- ``POST /projects/{id}/editor/undo``    retrocede en el historial.
- ``POST /projects/{id}/editor/redo``    avanza en el historial.
- ``GET  /projects/{id}/editor/history`` lista de versiones.
- ``POST /projects/{id}/editor/export``  encola el job ``edit_export`` (re-render).
- ``GET  /projects/{id}/editor/export``  estado del último export de edición.

El endpoint solo orquesta: la lógica de edición es pura (``engines.editor.operations``) y
el render pesado lo hace el ``edit_export_handler`` en el worker.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.editor.convert import timeline_to_edit_project
from app.engines.editor.model import EditProject, EditTrack
from app.engines.editor.operations import EditOperationError, apply_operation
from app.engines.editor.store import (
    history,
    load_active_edit_project,
    redo,
    save_edit_project,
    undo,
)
from app.engines.editor.subtitle_formats import (
    cues_to_subtitle_clips,
    dump_ass,
    dump_vtt,
    parse_ass,
    parse_srt,
    parse_vtt,
    subtitle_clips_to_cues,
)
from app.engines.render.timeline import TimelineBuilder
from app.engines.voice.subtitles import Cue, _fmt
from app.jobs.queue import JobQueue
from app.models.entities import Asset, Job, Project
from app.models.schemas import (
    EditExportResponse,
    EditOperationRequest,
    EditProjectRead,
)
from app.storage.db import get_db
from app.storage.media import ensure_project_workspace

router = APIRouter(tags=["editor"])

# Import/export de subtítulos (FEAT-003): formatos admitidos y validación.
_SUBTITLE_EXTS = {".srt", ".vtt", ".ass"}
# Tope razonable para un fichero de subtítulos de texto (5 MB).
_MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
# Content-type y extensión por formato de export.
_EXPORT_META = {
    "srt": ("application/x-subrip; charset=utf-8", "srt"),
    "vtt": ("text/vtt; charset=utf-8", "vtt"),
    "ass": ("text/x-ssa; charset=utf-8", "ass"),
}


def _dump_srt(cues: list[Cue]) -> str:
    """Serializa cues a SRT en memoria (mismo formato que ``voice.subtitles.write_srt``)."""
    ordered = sorted(cues, key=lambda c: c.t0)
    return "\n".join(
        f"{k + 1}\n{_fmt(c.t0)} --> {_fmt(c.t1)}\n{c.text}\n"
        for k, c in enumerate(ordered)
    )


def _get_project_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado"
        )
    return project


def _as_read(edit_project: EditProject) -> EditProjectRead:
    return EditProjectRead(
        project_id=edit_project.project_id,
        duration_s=edit_project.duration_s,
        source_timeline_version=edit_project.source_timeline_version,
        edit_project=edit_project.model_dump(mode="json", by_alias=False),
    )


@router.post(
    "/projects/{project_id}/editor/open",
    response_model=EditProjectRead,
)
def open_editor(project_id: int, session: Session = Depends(get_db)) -> EditProjectRead:
    """Abre el editor: devuelve el ``EditProject`` activo o lo deriva del último Timeline.

    Idempotente: si ya existe un ``EditProject`` activo lo devuelve sin regenerarlo; si no,
    construye el ``Timeline`` de generación, lo convierte a ``EditProject`` y lo persiste.
    """
    project = _get_project_or_404(session, project_id)

    existing = load_active_edit_project(project, session)
    if existing is not None:
        return _as_read(existing)

    timeline = TimelineBuilder().build(project, session)
    edit_project = timeline_to_edit_project(timeline, project, list(project.assets))
    saved = save_edit_project(project, session, edit_project)
    return _as_read(saved)


@router.get("/projects/{project_id}/editor", response_model=EditProjectRead)
def get_editor(project_id: int, session: Session = Depends(get_db)) -> EditProjectRead:
    """Devuelve el ``EditProject`` activo (404 si el editor no se ha abierto aún)."""
    project = _get_project_or_404(session, project_id)
    edit_project = load_active_edit_project(project, session)
    if edit_project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El editor no se ha abierto; usa POST .../editor/open primero.",
        )
    return _as_read(edit_project)


@router.put("/projects/{project_id}/editor", response_model=EditProjectRead)
def put_editor(
    project_id: int,
    body: dict = Body(...),
    session: Session = Depends(get_db),
) -> EditProjectRead:
    """Reemplaza el ``EditProject`` completo (guardado del estado editado) + Version."""
    project = _get_project_or_404(session, project_id)

    # Admite el documento directo o envuelto en ``{"edit_project": {...}}``.
    document = body.get("edit_project", body) if isinstance(body, dict) else body
    try:
        edit_project = EditProject.model_validate(document)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"EditProject inválido: {exc.errors()}",
        ) from exc

    if edit_project.project_id != project_id:
        edit_project = edit_project.model_copy(update={"project_id": project_id})

    saved = save_edit_project(project, session, edit_project)
    return _as_read(saved)


@router.post("/projects/{project_id}/editor/op", response_model=EditProjectRead)
def apply_op(
    project_id: int,
    request: EditOperationRequest,
    session: Session = Depends(get_db),
) -> EditProjectRead:
    """Aplica UNA operación pura sobre el ``EditProject`` activo, la persiste y versiona."""
    project = _get_project_or_404(session, project_id)
    edit_project = load_active_edit_project(project, session)
    if edit_project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El editor no se ha abierto; usa POST .../editor/open primero.",
        )

    try:
        updated = apply_operation(edit_project, request.op, request.params)
    except EditOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    saved = save_edit_project(project, session, updated)
    return _as_read(saved)


@router.post("/projects/{project_id}/editor/undo", response_model=EditProjectRead)
def undo_editor(project_id: int, session: Session = Depends(get_db)) -> EditProjectRead:
    """Deshace: hace activo el estado anterior del historial."""
    project = _get_project_or_404(session, project_id)
    restored = undo(project, session)
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No hay nada que deshacer.",
        )
    return _as_read(restored)


@router.post("/projects/{project_id}/editor/redo", response_model=EditProjectRead)
def redo_editor(project_id: int, session: Session = Depends(get_db)) -> EditProjectRead:
    """Rehace: avanza al estado siguiente del historial."""
    project = _get_project_or_404(session, project_id)
    restored = redo(project, session)
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No hay nada que rehacer.",
        )
    return _as_read(restored)


@router.get("/projects/{project_id}/editor/history")
def editor_history(project_id: int, session: Session = Depends(get_db)) -> list[dict]:
    """Lista las versiones del historial de edición (para una UI de historial)."""
    project = _get_project_or_404(session, project_id)
    return history(project, session)


@router.get("/projects/{project_id}/editor/subtitles/export")
def export_subtitles(
    project_id: int,
    format: str = Query("srt", pattern="^(srt|vtt|ass)$"),
    session: Session = Depends(get_db),
) -> Response:
    """Serializa la pista ``subtitle`` activa y devuelve ``subtitles.<ext>``.

    El contenido refleja los tiempos/texto EDITADOS en el editor (paridad editor↔
    subtítulos): extrae los cues de los clips de la pista ``subtitle`` del ``EditProject``
    activo y los vuelca en el formato pedido (SRT/VTT/ASS).
    """
    project = _get_project_or_404(session, project_id)
    edit_project = load_active_edit_project(project, session)
    if edit_project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El editor no se ha abierto; usa POST .../editor/open primero.",
        )

    cues = subtitle_clips_to_cues(edit_project)
    if format == "vtt":
        body = dump_vtt(cues)
    elif format == "ass":
        style = next(
            (
                c.style
                for t in edit_project.tracks
                if t.kind == "subtitle"
                for c in t.clips
                if c.style is not None
            ),
            None,
        )
        body = dump_ass(cues, style)
    else:
        body = _dump_srt(cues)

    content_type, ext = _EXPORT_META[format]
    return Response(
        content=body,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="subtitles.{ext}"'},
    )


@router.post("/projects/{project_id}/editor/subtitles/import", response_model=EditProjectRead)
def import_subtitles(
    project_id: int,
    file: UploadFile = File(...),
    mode: str = Query("replace", pattern="^(replace|append)$"),
    session: Session = Depends(get_db),
) -> EditProjectRead:
    """Importa un ``.srt/.vtt/.ass`` y reemplaza/añade la pista ``subtitle`` del editor.

    Valida formato por extensión + contenido (patrón de ``upload_scene_image``), parsea a
    cues, los convierte a clips de la pista ``subtitle``, crea una ``Version`` vía
    ``save_edit_project`` y guarda el fichero importado como ``Asset(kind='srt')`` para
    trazabilidad. Con ``mode=replace`` sustituye los clips de la pista; con ``append`` los
    añade a los existentes.
    """
    project = _get_project_or_404(session, project_id)
    edit_project = load_active_edit_project(project, session)
    if edit_project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El editor no se ha abierto; usa POST .../editor/open primero.",
        )

    original_name = file.filename or ""
    ext = Path(original_name).suffix.lower()
    if ext not in _SUBTITLE_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El fichero no es un subtítulo admitido. Formatos: SRT, VTT, ASS.",
        )

    raw = file.file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El fichero de subtítulos está vacío.",
        )
    if len(raw) > _MAX_SUBTITLE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"El subtítulo supera el tamaño máximo permitido "
                f"({_MAX_SUBTITLE_BYTES // (1024 * 1024)} MB)."
            ),
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El fichero de subtítulos no es texto UTF-8 válido.",
        ) from exc

    parser = {".srt": parse_srt, ".vtt": parse_vtt, ".ass": parse_ass}[ext]
    cues = parser(text)
    if not cues:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No se encontraron subtítulos en el fichero; comprueba que el formato "
                f"({ext[1:].upper()}) es correcto y no está vacío."
            ),
        )

    # Localiza (o crea) la pista subtitle del proyecto.
    updated = edit_project.model_copy(deep=True)
    track = next((t for t in updated.tracks if t.kind == "subtitle"), None)
    if track is None:
        order = max((t.order for t in updated.tracks), default=-1) + 1
        track = EditTrack(id="track_subtitle", kind="subtitle", name="Subtítulos", order=order)
        updated.tracks.append(track)

    new_clips = cues_to_subtitle_clips(cues, track.id, id_prefix=f"clip_sub_{uuid.uuid4().hex[:8]}")
    if mode == "replace":
        track.clips = new_clips
    else:
        track.clips = list(track.clips) + new_clips

    # Persiste el fichero importado como Asset(kind='srt') para trazabilidad.
    out_dir = ensure_project_workspace(project.id) / "srt"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"import_{uuid.uuid4().hex[:8]}{ext}"
    out_path.write_bytes(raw)
    session.add(
        Asset(
            project_id=project.id,
            scene_id=None,
            kind="srt",
            path=str(out_path),
            provider="import",
            meta={
                "source": "import",
                "format": ext[1:],
                "original_filename": original_name or None,
                "mode": mode,
                "cues": len(new_clips),
            },
        )
    )
    session.commit()

    saved = save_edit_project(project, session, updated)
    return _as_read(saved)


@router.post(
    "/projects/{project_id}/editor/export",
    response_model=EditExportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_edit_export(
    project_id: int, session: Session = Depends(get_db)
) -> EditExportResponse:
    """Encola el job ``edit_export`` que re-renderiza desde el ``EditProject`` activo."""
    project = _get_project_or_404(session, project_id)
    if load_active_edit_project(project, session) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El editor no se ha abierto; usa POST .../editor/open antes de exportar.",
        )

    job_id = JobQueue().enqueue(
        "edit_export", project_id=project_id, payload={"project_id": project_id}
    )
    job = session.get(Job, job_id)
    if job is None:  # pragma: no cover - acabamos de crearlo
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo crear el job de export de edición.",
        )
    return EditExportResponse(job_id=job.id, status=job.status, type=job.type)


@router.get("/projects/{project_id}/editor/export", response_model=dict)
def latest_edit_export(project_id: int, session: Session = Depends(get_db)) -> dict:
    """Estado del último export de edición del proyecto (para polling)."""
    _get_project_or_404(session, project_id)
    stmt = (
        select(Job)
        .where(Job.project_id == project_id, Job.type == "edit_export")
        .order_by(Job.id.desc())
        .limit(1)
    )
    job = session.execute(stmt).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El proyecto no tiene ningún export de edición; dispara POST .../editor/export.",
        )
    return {
        "id": job.id,
        "project_id": job.project_id,
        "type": job.type,
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "result": job.result,
    }
