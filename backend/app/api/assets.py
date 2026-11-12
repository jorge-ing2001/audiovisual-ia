"""Router REST de assets (listar y descargar).

Los assets los producen los motores (FEAT-003..007) y se guardan en el workspace
de media del proyecto. Aquí se listan y se sirven para descarga/preview.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Asset, Project
from app.models.schemas import AssetRead
from app.storage.db import get_db

router = APIRouter(tags=["assets"])


@router.get("/projects/{project_id}/assets", response_model=list[AssetRead])
def list_assets(
    project_id: int,
    kind: str | None = None,
    session: Session = Depends(get_db),
) -> list[Asset]:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proyecto no encontrado")
    stmt = select(Asset).where(Asset.project_id == project_id)
    if kind is not None:
        stmt = stmt.where(Asset.kind == kind)
    return list(session.execute(stmt.order_by(Asset.id.asc())).scalars())


@router.get("/assets/{asset_id}/download")
def download_asset(asset_id: int, session: Session = Depends(get_db)) -> FileResponse:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset no encontrado")
    path = Path(asset.path)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El fichero del asset no está disponible en el workspace",
        )
    return FileResponse(path, filename=path.name)
