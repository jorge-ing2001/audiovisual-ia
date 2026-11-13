"""Motor 1 — IA/guion (Fase 1).

Interpreta el prompt y genera guion -> escenas -> storyboard en JSON estructurado.
Interfaz: ``ScriptEngine.generate_script(project, session, options) -> ScriptResult``.
Implementado en FEAT-003 (resuelve el LLM activo vía registry; default Ollama).
"""

from __future__ import annotations

from app.engines.script.engine import ScriptEngine, ScriptResult

__all__ = ["ScriptEngine", "ScriptResult"]
