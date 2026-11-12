"""Motor 6 — Composición/Render (Fase 1).

Ensambla el timeline JSON (``timeline.py``), compone en Remotion y muxea con FFmpeg a
MP4 (``engine.py``). API pública: ``TimelineBuilder``/``Timeline`` y
``RenderEngine.compose(project, session) -> RenderResult``.
"""

from __future__ import annotations

from app.engines.render.engine import (
    FfmpegError,
    RenderEngine,
    RenderResult,
    RenderUnavailable,
)
from app.engines.render.timeline import (
    SubtitleCue,
    Timeline,
    TimelineBuilder,
    TimelineScene,
)

__all__ = [
    "TimelineBuilder",
    "Timeline",
    "TimelineScene",
    "SubtitleCue",
    "RenderEngine",
    "RenderResult",
    "RenderUnavailable",
    "FfmpegError",
]
