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

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.editor.convert import timeline_to_edit_project
from app.engines.editor.model import EditProject
from app.engines.editor.operations import EditOperationError, apply_operation
from app.engines.editor.store import (
    history,
    load_active_edit_project,
    redo,
    save_edit_project,
    undo,
)
from app.engines.render.timeline import TimelineBuilder
from app.jobs.queue import JobQueue
from app.models.entities import Job, Project
from app.models.schemas import (
    EditExportResponse,
    EditOperationRequest,
    EditProjectRead,
)
from app.storage.db import get_db

router = APIRouter(tags=["editor"])


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
