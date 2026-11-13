"""Motor de edición multipista (editor tipo CapCut).

Hermano del motor ``render``: NO sustituye el pipeline de generación
(``TimelineBuilder`` + composición ``Timeline``), lo complementa. Introduce un modelo
de proyecto de edición multipista (``EditProject``) independiente del ``Timeline`` por
escena, operaciones puras de edición, un compilador a ``inputProps`` de Remotion +
``MixTrack`` del ``AudioMixer``, persistencia/versionado sobre las tablas ``Timeline`` y
``Version`` existentes (sin migraciones) y un export que re-renderiza DESDE el estado
editado reutilizando ``RenderEngine``/``AudioMixer``/mux.

Ver ``docs/EDITOR-MULTITRACK-ARCHITECTURE.md`` (secciones 3, 4 y 7).
"""

from __future__ import annotations

from app.engines.editor.model import (
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
)

__all__ = [
    "EditProject",
    "EditTrack",
    "EditClip",
    "EditMeta",
]
