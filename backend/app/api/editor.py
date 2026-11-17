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
    DEFAULT_SEQUENCE_ID,
    create_sequence,
    history,
    list_sequences,
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
from app.engines.qa.suggestions import build_suggestions
from app.engines.render.timeline import TimelineBuilder
from app.engines.voice.master import FfmpegError
from app.engines.voice.subtitles import Cue, _fmt
from app.engines.voice.trim import SilenceTrimmer
from app.jobs.queue import JobQueue
from app.models.entities import Asset, Job, Project, Scene
from app.models.schemas import (
    AssetRead,
    EditExportResponse,
    EditOperationRequest,
    EditProjectRead,
)
from app.storage.db import get_db
from app.storage.media import ensure_project_workspace
from app.storage.uploads import (
    ALLOWED_AUDIO_CONTENT_TYPES,
    ALLOWED_AUDIO_EXTS,
    ALLOWED_IMAGE_CONTENT_TYPES,
    ALLOWED_IMAGE_EXTS,
    ALLOWED_VIDEO_CONTENT_TYPES,
    ALLOWED_VIDEO_EXTS,
    MAX_MEDIA_UPLOAD_BYTES,
    MAX_UPLOAD_BYTES,
    MediaProbeError,
    probe_media,
    safe_stem,
)

router = APIRouter(tags=["editor"])

# Importar medios al editor (FEAT-007): roles de audio admitidos y mapeo a Asset.kind.
_AUDIO_ROLES = {"voice", "music", "sfx"}

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
def open_editor(
    project_id: int,
    sequence: str = Query(DEFAULT_SEQUENCE_ID),
    session: Session = Depends(get_db),
) -> EditProjectRead:
    """Abre el editor: devuelve el ``EditProject`` activo o lo deriva del último Timeline.

    Idempotente: si ya existe un ``EditProject`` activo (de la secuencia pedida) lo devuelve
    sin regenerarlo; si no, construye el ``Timeline`` de generación, lo convierte a
    ``EditProject`` y lo persiste. Sin ``sequence`` opera sobre ``main`` (regresión).
    """
    project = _get_project_or_404(session, project_id)

    existing = load_active_edit_project(project, session, sequence)
    if existing is not None:
        return _as_read(existing)

    timeline = TimelineBuilder().build(project, session)
    edit_project = timeline_to_edit_project(timeline, project, list(project.assets))
    if sequence != DEFAULT_SEQUENCE_ID:
        edit_project = edit_project.model_copy(update={"sequence_id": sequence})
    saved = save_edit_project(project, session, edit_project, sequence_id=sequence)
    return _as_read(saved)


@router.get("/projects/{project_id}/editor", response_model=EditProjectRead)
def get_editor(
    project_id: int,
    sequence: str = Query(DEFAULT_SEQUENCE_ID),
    session: Session = Depends(get_db),
) -> EditProjectRead:
    """Devuelve el ``EditProject`` activo de la secuencia (404 si el editor no se abrió)."""
    project = _get_project_or_404(session, project_id)
    edit_project = load_active_edit_project(project, session, sequence)
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
    sequence: str = Query(DEFAULT_SEQUENCE_ID),
    session: Session = Depends(get_db),
) -> EditProjectRead:
    """Reemplaza el ``EditProject`` completo (guardado del estado editado) + Version.

    El parámetro ``sequence`` (default ``main``) elige la secuencia sobre la que se guarda;
    si el body trae ``sequence_id`` distinto, manda el del query (fuente de verdad del
    endpoint). Sin el parámetro, comportamiento idéntico al actual.
    """
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

    updates: dict = {}
    if edit_project.project_id != project_id:
        updates["project_id"] = project_id
    if edit_project.sequence_id != sequence:
        updates["sequence_id"] = sequence
    if updates:
        edit_project = edit_project.model_copy(update=updates)

    saved = save_edit_project(project, session, edit_project, sequence_id=sequence)
    return _as_read(saved)


@router.post("/projects/{project_id}/editor/op", response_model=EditProjectRead)
def apply_op(
    project_id: int,
    request: EditOperationRequest,
    sequence: str = Query(DEFAULT_SEQUENCE_ID),
    session: Session = Depends(get_db),
) -> EditProjectRead:
    """Aplica UNA operación pura sobre el ``EditProject`` activo, la persiste y versiona.

    Opera sobre la secuencia ``sequence`` (default ``main``); sin el parámetro, idéntico
    al actual.
    """
    project = _get_project_or_404(session, project_id)
    edit_project = load_active_edit_project(project, session, sequence)
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

    saved = save_edit_project(project, session, updated, sequence_id=sequence)
    return _as_read(saved)


@router.post("/projects/{project_id}/editor/undo", response_model=EditProjectRead)
def undo_editor(
    project_id: int,
    sequence: str = Query(DEFAULT_SEQUENCE_ID),
    session: Session = Depends(get_db),
) -> EditProjectRead:
    """Deshace: hace activo el estado anterior del historial de la secuencia."""
    project = _get_project_or_404(session, project_id)
    restored = undo(project, session, sequence)
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No hay nada que deshacer.",
        )
    return _as_read(restored)


@router.post("/projects/{project_id}/editor/redo", response_model=EditProjectRead)
def redo_editor(
    project_id: int,
    sequence: str = Query(DEFAULT_SEQUENCE_ID),
    session: Session = Depends(get_db),
) -> EditProjectRead:
    """Rehace: avanza al estado siguiente del historial de la secuencia."""
    project = _get_project_or_404(session, project_id)
    restored = redo(project, session, sequence)
    if restored is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No hay nada que rehacer.",
        )
    return _as_read(restored)


@router.get("/projects/{project_id}/editor/history")
def editor_history(
    project_id: int,
    sequence: str = Query(DEFAULT_SEQUENCE_ID),
    session: Session = Depends(get_db),
) -> list[dict]:
    """Lista las versiones del historial de edición de la secuencia (para la UI)."""
    project = _get_project_or_404(session, project_id)
    return history(project, session, sequence)


@router.get("/projects/{project_id}/editor/sequences")
def editor_sequences(project_id: int, session: Session = Depends(get_db)) -> list[dict]:
    """Lista las secuencias del proyecto (id/name/duración/versión activa) (FEAT-008)."""
    project = _get_project_or_404(session, project_id)
    return list_sequences(project, session)


@router.post(
    "/projects/{project_id}/editor/sequences",
    response_model=EditProjectRead,
    status_code=status.HTTP_201_CREATED,
)
def create_editor_sequence(
    project_id: int,
    body: dict = Body(default_factory=dict),
    session: Session = Depends(get_db),
) -> EditProjectRead:
    """Crea una secuencia nueva (FEAT-008), devolviendo su ``EditProject`` inicial.

    Body: ``sequence_id`` (obligatorio), ``name`` (opcional) y ``from`` (``active``, por
    defecto, duplica la secuencia activa indicada en ``base_sequence`` -default ``main``-;
    o ``timeline``, deriva del ``Timeline`` de generación como ``open``). Rechaza 409 si la
    secuencia ya existe y 422 si falta el punto de partida.
    """
    project = _get_project_or_404(session, project_id)

    sequence_id = body.get("sequence_id")
    if not isinstance(sequence_id, str) or not sequence_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'sequence_id' (str) es obligatorio.",
        )
    name = body.get("name")
    if name is not None and not isinstance(name, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'name' debe ser str.",
        )

    source = body.get("from", "active")
    base_from_active = None
    from_timeline = None
    if source == "timeline":
        timeline = TimelineBuilder().build(project, session)
        from_timeline = timeline_to_edit_project(timeline, project, list(project.assets))
    else:
        base_sequence = body.get("base_sequence", DEFAULT_SEQUENCE_ID)
        if not isinstance(base_sequence, str) or not base_sequence:
            base_sequence = DEFAULT_SEQUENCE_ID
        base_from_active = load_active_edit_project(project, session, base_sequence)
        if base_from_active is None:
            # Sin secuencia base activa: deriva del Timeline (como ``open``).
            timeline = TimelineBuilder().build(project, session)
            from_timeline = timeline_to_edit_project(
                timeline, project, list(project.assets)
            )

    try:
        created = create_sequence(
            project,
            session,
            sequence_id,
            name,
            base_from_active=base_from_active,
            from_timeline=from_timeline,
        )
    except ValueError as exc:
        detail = str(exc)
        code = (
            status.HTTP_409_CONFLICT
            if "ya existe" in detail
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(status_code=code, detail=detail) from exc
    return _as_read(created)


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


def _detect_media_kind(ext: str, content_type: str, role: str) -> str:
    """Detecta el ``Asset.kind`` del fichero por extensión + content-type.

    Imagen → ``image``; vídeo → ``video``; audio → el ``role`` (``voice``/``music``/
    ``sfx``, default ``music``). Lanza ``HTTPException`` 400 si no es un medio admitido.
    """
    is_image = ext in ALLOWED_IMAGE_EXTS or content_type in ALLOWED_IMAGE_CONTENT_TYPES
    is_video = ext in ALLOWED_VIDEO_EXTS or content_type in ALLOWED_VIDEO_CONTENT_TYPES
    is_audio = ext in ALLOWED_AUDIO_EXTS or content_type in ALLOWED_AUDIO_CONTENT_TYPES
    # Prioridad por extensión para evitar ambigüedad de content-types genéricos.
    if ext in ALLOWED_IMAGE_EXTS:
        return "image"
    if ext in ALLOWED_VIDEO_EXTS:
        return "video"
    if ext in ALLOWED_AUDIO_EXTS:
        return role
    if is_image:
        return "image"
    if is_video:
        return "video"
    if is_audio:
        return role
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "El fichero no es un medio admitido. Formatos: imagen "
            "(PNG/JPG/JPEG/WEBP/GIF), vídeo (MP4/MOV/WEBM/MKV) o audio "
            "(MP3/WAV/M4A/AAC/OGG/FLAC)."
        ),
    )


@router.post(
    "/projects/{project_id}/editor/media/upload",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_editor_media(
    project_id: int,
    file: UploadFile = File(...),
    role: str = Query(
        "music",
        pattern="^(voice|music|sfx)$",
        description="Rol de un AUDIO importado (voice/music/sfx); ignorado para imagen/vídeo.",
    ),
    session: Session = Depends(get_db),
) -> Asset:
    """Importa un medio (audio/vídeo/imagen) a nivel de PROYECTO para el editor (FEAT-007).

    Detecta el ``kind`` por extensión + content-type: imagen (``image``), vídeo
    (``video``) o audio (mapeado a ``role`` ∈ ``voice``/``music``/``sfx``, default
    ``music``). Valida la imagen con Pillow (patrón de ``upload_scene_image``) y el
    audio/vídeo con **ffprobe** (``AIV_FFPROBE_BIN``): confirma streams válidos y lee
    ``duration_s``. Si ffprobe no está disponible, rechaza con mensaje accionable (no
    finge). Guarda el fichero bajo ``media/uploads/`` del workspace (dentro de
    ``AIV_MEDIA_ROOT``, servible por ``GET /assets/{id}/download`` con Range, que usa el
    Player) y crea ``Asset(kind correcto, provider='upload')`` con ``duration_s``/
    dimensiones en ``meta``. Devuelve ``AssetRead``. NO crea el clip: el frontend llama a
    ``op add_media_clip`` tras subir (I/O y colocación separadas, §6.1).
    """
    project = _get_project_or_404(session, project_id)

    if role not in _AUDIO_ROLES:  # defensivo (el pattern ya lo valida)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El parámetro 'role' debe ser 'voice', 'music' o 'sfx'.",
        )

    original_name = file.filename or ""
    ext = Path(original_name).suffix.lower()
    content_type = (file.content_type or "").lower()
    kind = _detect_media_kind(ext, content_type, role)

    data = file.file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El fichero subido está vacío.",
        )
    # Tope de tamaño: imagen 15 MB, audio/vídeo (media) 200 MB.
    max_bytes = MAX_UPLOAD_BYTES if kind == "image" else MAX_MEDIA_UPLOAD_BYTES
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"El fichero supera el tamaño máximo permitido "
                f"({max_bytes // (1024 * 1024)} MB)."
            ),
        )

    # Guarda el fichero en media/uploads/ (bajo AIV_MEDIA_ROOT) con nombre seguro y único.
    out_dir = ensure_project_workspace(project.id) / "media" / "uploads"
    out_dir.mkdir(parents=True, exist_ok=True)
    if kind == "image":
        safe_ext = ext if ext in ALLOWED_IMAGE_EXTS else ".png"
    elif kind == "video":
        safe_ext = ext if ext in ALLOWED_VIDEO_EXTS else ".mp4"
    else:
        safe_ext = ext if ext in ALLOWED_AUDIO_EXTS else ".mp3"
    filename = f"{kind}_{safe_stem(original_name)}_{uuid.uuid4().hex[:8]}{safe_ext}"
    out_path = out_dir / filename
    out_path.write_bytes(data)

    meta: dict = {
        "source": "upload",
        "original_filename": original_name or None,
        "content_type": content_type or None,
        "bytes": len(data),
    }

    if kind == "image":
        # Validación del contenido real + dimensiones con Pillow.
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(data)) as img:
                img.verify()
            with Image.open(io.BytesIO(data)) as img2:
                width, height = img2.size
        except Exception as exc:  # noqa: BLE001 - cualquier fallo = no es imagen válida
            out_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El fichero no es una imagen válida o está dañado.",
            ) from exc
        meta.update({"width": width, "height": height})
    else:
        # Audio/vídeo: validación con ffprobe (streams + duración). Si ffprobe no está,
        # se rechaza con mensaje accionable (no se finge la validación).
        try:
            probe = probe_media(out_path)
        except MediaProbeError as exc:
            out_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        if kind == "video" and not probe.has_video:
            out_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El fichero no contiene un stream de vídeo válido.",
            )
        if kind in _AUDIO_ROLES and not probe.has_audio:
            out_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El fichero no contiene un stream de audio válido.",
            )
        meta.update(
            {
                "duration_s": probe.duration_s,
                "width": probe.width,
                "height": probe.height,
                "has_video": probe.has_video,
                "has_audio": probe.has_audio,
            }
        )
        if kind in _AUDIO_ROLES:
            meta["role"] = role

    asset = Asset(
        project_id=project.id,
        scene_id=None,
        kind=kind,
        path=str(out_path),
        provider="upload",
        meta=meta,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


@router.post(
    "/projects/{project_id}/editor/export",
    response_model=EditExportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_edit_export(
    project_id: int,
    sequence: str = Query(DEFAULT_SEQUENCE_ID),
    session: Session = Depends(get_db),
) -> EditExportResponse:
    """Encola el job ``edit_export`` que re-renderiza desde el ``EditProject`` de la secuencia."""
    project = _get_project_or_404(session, project_id)
    if load_active_edit_project(project, session, sequence) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El editor no se ha abierto; usa POST .../editor/open antes de exportar.",
        )

    job_id = JobQueue().enqueue(
        "edit_export",
        project_id=project_id,
        payload={"project_id": project_id, "sequence": sequence},
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


# --------------------------------------------------------------------------- #
# Edición inteligente (FEAT-006): análisis de silencios + sugerencias
# --------------------------------------------------------------------------- #


@router.get("/projects/{project_id}/editor/silences")
def editor_silences(
    project_id: int,
    clip_id: str = Query(..., description="Id del clip de voz a analizar."),
    silence_db: float = Query(-35.0),
    min_silence: float = Query(0.35),
    sequence: str = Query(DEFAULT_SEQUENCE_ID),
    session: Session = Depends(get_db),
) -> dict:
    """Detecta los tramos de silencio del audio de un clip de voz (preview del auto-corte).

    La detección REAL usa ffmpeg (``silencedetect``) sobre el fichero del ``Asset`` del
    clip; si ffmpeg no está disponible, responde ``{"status": "skipped", ...}`` con la
    razón (nunca inventa tramos). Los tramos se devuelven en el eje del ORIGEN del audio,
    listos para pasarse como ``silences`` a la operación ``auto_trim_silence`` (que es pura
    y reversible vía ``undo``).
    """
    project = _get_project_or_404(session, project_id)
    edit_project = load_active_edit_project(project, session, sequence)
    if edit_project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El editor no se ha abierto; usa POST .../editor/open primero.",
        )
    found = edit_project.find_clip(clip_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe el clip {clip_id!r} en el editor.",
        )
    _, clip = found
    if clip.asset_id is None:
        return {
            "status": "skipped",
            "clip_id": clip_id,
            "detail": "El clip no referencia ningún asset de audio.",
            "silences": [],
        }
    asset = session.get(Asset, clip.asset_id)
    if asset is None or not asset.path or not Path(asset.path).is_file():
        return {
            "status": "skipped",
            "clip_id": clip_id,
            "detail": "El fichero de audio del clip no está disponible en disco.",
            "silences": [],
        }

    try:
        silences = SilenceTrimmer()._detect_silences(asset.path, silence_db, min_silence)
    except FfmpegError as exc:
        return {
            "status": "skipped",
            "clip_id": clip_id,
            "detail": str(exc),
            "silences": [],
        }
    return {
        "status": "ok",
        "clip_id": clip_id,
        "silences": [[round(s, 3), round(e, 3)] for s, e in silences],
    }


@router.get("/projects/{project_id}/suggestions")
def project_suggestions(project_id: int, session: Session = Depends(get_db)) -> list[dict]:
    """Devuelve las sugerencias deterministas del proyecto (FEAT-006, §3.2).

    Lee el estado (escenas + assets de generación y el ``EditProject`` activo si existe) y
    delega en ``engines/qa/suggestions.py`` (lógica PURA, sin ffmpeg): escenas sin imagen,
    escenas con duración inválida, subtítulos que exceden la duración, clips solapados y
    (si procede) proyecto sin voz. Cada aviso tiene el shape ``{id,label,status,detail}``
    de los checks del QA.
    """
    project = _get_project_or_404(session, project_id)

    # Escenas de generación con un flag ``has_image`` resuelto desde los Assets.
    scene_rows = (
        session.execute(select(Scene).where(Scene.project_id == project_id)).scalars().all()
    )
    image_scene_ids = {
        a.scene_id
        for a in session.execute(
            select(Asset).where(Asset.project_id == project_id, Asset.kind == "image")
        )
        .scalars()
        .all()
        if a.scene_id is not None
    }
    scenes = [
        {
            "id": s.id,
            "order": s.order,
            "duration_s": s.duration_s,
            "has_image": s.id in image_scene_ids,
        }
        for s in scene_rows
    ]

    # Estado del editor (si se abrió): pistas/clips y duración total de referencia.
    edit_project = load_active_edit_project(project, session)
    tracks: list[dict] = []
    reference_duration_s: float | None = None
    if edit_project is not None:
        reference_duration_s = edit_project.duration_s
        for track in edit_project.tracks:
            tracks.append(
                {
                    "id": track.id,
                    "kind": track.kind,
                    "clips": [
                        {
                            "id": c.id,
                            "start_s": c.start_s,
                            "duration_s": c.duration_s,
                            "enabled": c.enabled,
                        }
                        for c in track.clips
                    ],
                }
            )

    return build_suggestions(scenes, tracks, reference_duration_s)
