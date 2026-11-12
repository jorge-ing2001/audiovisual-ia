"""Motor 2 — Visual 2D (``VisualEngine``).

Obtiene/genera la imagen de una escena usando el ``ImageProvider`` activo del
registry (default ``stock`` con degradación a ``placeholder``), escribe el fichero
en el workspace del proyecto y crea un ``Asset(kind='image')`` con el provider y la
atribución de stock en ``meta``.

Cache (§5/§8 de la arquitectura): antes de llamar al proveedor calcula un hash de
``(visual_prompt, aspect, backend, seed)``. Si ya existe un ``Asset`` válido con
ese hash y su fichero sigue en disco, lo reutiliza en vez de volver a llamar al
proveedor (evita llamadas costosas innecesarias).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Asset, Project, Scene
from app.providers import registry
from app.storage.media import ensure_project_workspace


def cache_key(visual_prompt: str, aspect: str, backend: str, seed: int | None) -> str:
    """Hash estable de la entrada de generación de imagen (clave de cache)."""
    raw = f"{visual_prompt}|{aspect}|{backend}|{seed}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class VisualEngine:
    """Motor visual 2D: escena -> imagen (provider del registry) -> Asset en BD."""

    def render_scene_image(
        self,
        scene: Scene,
        session: Session,
        *,
        style: str | None = None,
        seed: int | None = None,
        force: bool = False,
    ) -> Asset:
        """Genera (o reutiliza de cache) la imagen de ``scene`` y crea un ``Asset``.

        Con ``force=True`` se ignora la cache y se regenera. ``style`` se concatena
        al ``visual_prompt`` como guía de estilo para el proveedor.
        """
        project = session.get(Project, scene.project_id)
        if project is None:  # pragma: no cover - integridad referencial garantizada
            raise ValueError(f"Proyecto {scene.project_id} inexistente")

        aspect = project.aspect_ratio
        backend = registry.active_backend("image", project.provider_overrides)
        query = scene.visual_prompt.strip()
        if style:
            query = f"{query}, {style}".strip(", ")

        key = cache_key(query, aspect, backend, seed)

        if not force:
            cached = self._find_cached(session, scene, key)
            if cached is not None:
                return cached

        out_dir = ensure_project_workspace(project.id) / "images"
        provider = registry.resolve("image", project.provider_overrides)
        image = provider.image_for(query, aspect=aspect, out_dir=str(out_dir), seed=seed)

        meta = dict(image.meta or {})
        meta.update(
            {
                "cache_key": key,
                "aspect": aspect,
                "backend": backend,
                "seed": seed,
                "width": image.width,
                "height": image.height,
                "query": query,
            }
        )
        asset = Asset(
            project_id=project.id,
            scene_id=scene.id,
            kind="image",
            path=image.path,
            provider=image.provider,
            meta=meta,
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset

    @staticmethod
    def _find_cached(session: Session, scene: Scene, key: str) -> Asset | None:
        """Devuelve un Asset de imagen con ese cache_key cuyo fichero exista."""
        stmt = (
            select(Asset)
            .where(Asset.scene_id == scene.id, Asset.kind == "image")
            .order_by(Asset.id.desc())
        )
        for asset in session.execute(stmt).scalars():
            if (asset.meta or {}).get("cache_key") == key and Path(asset.path).is_file():
                return asset
        return None
