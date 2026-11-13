"""Router REST de generación (motor de guion y de imagen de escena) — FEAT-003 / FEAT-B.

- ``POST /projects/{id}/script`` ejecuta el motor de guion (motor 1) y devuelve las
  escenas persistidas.
- ``POST /scenes/{scene_id}/image`` (re)genera la imagen de una escena con el motor
  visual 2D (motor 2), reutilizando la cache salvo ``force=true``.
- ``POST /scenes/{scene_id}/image/upload`` (FEAT-B) sube una imagen propia del usuario,
  la guarda en el workspace del proyecto y crea un ``Asset(kind='image')`` propio
  (``provider='upload'``). Al ser el asset de imagen más reciente de la escena, el
  ``TimelineBuilder`` lo usa automáticamente en producción/edición (reemplazo aislado).

Los motores resuelven sus proveedores vía el registry; este router solo orquesta.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.script import ScriptEngine
from app.engines.visual2d import VisualEngine
from app.jobs.pipeline import enqueue_generate_video
from app.models.entities import Asset, Job, Project, Scene
from app.models.schemas import (
    AssetRead,
    GenerateVideoRequest,
    GenerateVideoResponse,
    SceneImageRequest,
    SceneRead,
    ScriptGenerateRequest,
    ScriptGenerateResponse,
)
from app.storage.db import get_db
from app.storage.media import ensure_project_workspace

router = APIRouter(tags=["generation"])

# Validación de subida de imagen propia (FEAT-B).
# Extensiones y content-types admitidos para recursos visuales del usuario.
_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}
# Tamaño máximo razonable de una imagen subida (15 MB).
_MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def _safe_stem(name: str) -> str:
    """Normaliza el nombre original a un stem seguro para el sistema de ficheros."""
    stem = Path(name or "").stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem[:64] or "imagen"


@router.post(
    "/projects/{project_id}/script",
    response_model=ScriptGenerateResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_script(
    project_id: int,
    body: ScriptGenerateRequest | None = None,
    session: Session = Depends(get_db),
) -> ScriptGenerateResponse:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado")

    body = body or ScriptGenerateRequest()
    if body.prompt is not None:
        project.prompt = body.prompt
        session.commit()

    if not (project.prompt or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El proyecto no tiene prompt; envía 'prompt' en el cuerpo o actualízalo antes.",
        )

    try:
        result = ScriptEngine().generate_script(project, session, options=body.options)
    except ValueError as exc:
        # Salida del LLM inválida / no parseable.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    stmt = (
        select(Scene)
        .where(Scene.project_id == project_id)
        .order_by(Scene.order.asc(), Scene.id.asc())
    )
    scenes = list(session.execute(stmt).scalars())
    return ScriptGenerateResponse(
        script_id=result.script_id,
        scenes=[SceneRead.model_validate(s) for s in scenes],
    )


@router.post(
    "/scenes/{scene_id}/image",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
def generate_scene_image(
    scene_id: int,
    body: SceneImageRequest | None = None,
    session: Session = Depends(get_db),
) -> Asset:
    scene = session.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escena no encontrada")

    body = body or SceneImageRequest()
    return VisualEngine().render_scene_image(
        scene, session, style=body.style, seed=body.seed, force=body.force
    )


@router.post(
    "/scenes/{scene_id}/image/upload",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_scene_image(
    scene_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
) -> Asset:
    """Sube una imagen propia del usuario y la asocia a la escena (FEAT-B).

    Valida que el fichero es una imagen (content-type ``image/*`` y/o extensión
    admitida) y que no excede ``_MAX_UPLOAD_BYTES``. Guarda el fichero DENTRO del
    workspace del proyecto (``images/uploads/``) con un nombre seguro y único para
    que quede bajo ``AIV_MEDIA_ROOT`` (Remotion lo sirve con ``staticFile``). Crea un
    ``Asset(kind='image', provider='upload')`` que, por ser el más reciente de la
    escena, el ``TimelineBuilder`` usará en la composición sin afectar a otras escenas.
    """
    scene = session.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escena no encontrada")

    project = session.get(Project, scene.project_id)
    if project is None:  # pragma: no cover - integridad referencial garantizada
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado")

    original_name = file.filename or ""
    ext = Path(original_name).suffix.lower()
    content_type = (file.content_type or "").lower()

    # Validación de tipo: admitimos si el content-type es de imagen o si la extensión
    # está en la lista blanca (algunos navegadores no envían content-type fiable).
    is_image_ct = content_type.startswith("image/") and (
        content_type in _ALLOWED_IMAGE_CONTENT_TYPES
    )
    is_image_ext = ext in _ALLOWED_IMAGE_EXTS
    if not (is_image_ct or is_image_ext):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "El fichero no es una imagen admitida. Formatos permitidos: "
                "PNG, JPG, JPEG, WEBP, GIF."
            ),
        )

    data = file.file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El fichero subido está vacío.",
        )
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,  # Payload Too Large (constante de starlette deprecada)
            detail=(
                f"La imagen supera el tamaño máximo permitido "
                f"({_MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."
            ),
        )

    # Validación del contenido real + dimensiones con Pillow (defensa frente a ficheros
    # con extensión de imagen pero contenido corrupto/no-imagen).
    width: int | None = None
    height: int | None = None
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img2:
            width, height = img2.size
    except Exception as exc:  # noqa: BLE001 - cualquier fallo = no es una imagen válida
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El fichero no es una imagen válida o está dañado.",
        ) from exc

    out_dir = ensure_project_workspace(project.id) / "images" / "uploads"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_ext = ext if ext in _ALLOWED_IMAGE_EXTS else ".png"
    filename = f"scene_{scene.id}_{_safe_stem(original_name)}_{uuid.uuid4().hex[:8]}{safe_ext}"
    out_path = out_dir / filename
    out_path.write_bytes(data)

    asset = Asset(
        project_id=project.id,
        scene_id=scene.id,
        kind="image",
        path=str(out_path),
        provider="upload",
        meta={
            "source": "upload",
            "original_filename": original_name or None,
            "content_type": content_type or None,
            "width": width,
            "height": height,
            "bytes": len(data),
        },
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


@router.post(
    "/projects/{project_id}/generate",
    response_model=GenerateVideoResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_video(
    project_id: int,
    body: GenerateVideoRequest | None = None,
    session: Session = Depends(get_db),
) -> GenerateVideoResponse:
    """Encola el pipeline end-to-end prompt->video (FEAT-006) y devuelve el job.

    El trabajo pesado (guion, imagen/voz por escena, música/SFX, subtítulos, timeline y
    render+mux) lo ejecuta ``generate_video_handler`` en el worker. El estado y el progreso
    por paso se consultan con ``GET /jobs/{id}``.
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado")

    body = body or GenerateVideoRequest()
    if body.prompt is not None:
        project.prompt = body.prompt
        session.commit()

    # Validación de prompt (§8): sin prompt, sin guion previo y sin fixture no hay entrada.
    has_prompt = bool((project.prompt or "").strip())
    if not project.scenes and body.script_fixture is None and not has_prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "El proyecto no tiene prompt ni guion; envía 'prompt' (o un "
                "'script_fixture' etiquetado) para generar el vídeo."
            ),
        )

    options = {
        "style": body.style,
        "script_fixture": body.script_fixture,
        "script_options": body.script_options,
        "audio_tracks": body.audio_tracks,
    }
    job_id = enqueue_generate_video(project_id, options=options)
    job = session.get(Job, job_id)
    if job is None:  # pragma: no cover - acabamos de crearlo
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo crear el job de generación.",
        )
    return GenerateVideoResponse(job_id=job.id, status=job.status, type=job.type)
