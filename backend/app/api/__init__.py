"""Routers REST (projects, scenes, assets, jobs).

Implementados en FEAT-002 y montados en ``app.main.create_app``. Los routers de
generation/render se añaden en features posteriores.
"""

from __future__ import annotations

from app.api import assets, jobs, projects, scenes

__all__ = ["projects", "scenes", "assets", "jobs"]
