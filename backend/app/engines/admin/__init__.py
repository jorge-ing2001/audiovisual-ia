"""Motor de administración de proyectos (Bloque 4).

Agrupa la lógica de administración sobre proyectos existentes que NO pertenece al
pipeline de generación ni al editor: duplicación profunda con identidades nuevas y
preservación de relaciones, y utilidades de borrado seguro del workspace de media.

El plan de duplicación (``build_duplication_plan``) es PURO (no toca disco ni BD) y por
tanto testeable de forma aislada; su ejecución (``run_duplication_plan``) aplica el plan
sobre una sesión SQLAlchemy y el sistema de ficheros con rollback + limpieza ante fallo.

Ver ``docs/ADMIN-QUALITY.md`` §1.
"""

from __future__ import annotations

from app.engines.admin.duplicate import (
    DuplicationPlan,
    PlannedRow,
    build_duplication_plan,
    run_duplication_plan,
)

__all__ = [
    "DuplicationPlan",
    "PlannedRow",
    "build_duplication_plan",
    "run_duplication_plan",
]
