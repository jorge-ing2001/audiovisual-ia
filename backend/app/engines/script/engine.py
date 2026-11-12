"""Motor 1 — IA/guion (``ScriptEngine``).

Interpreta el prompt del usuario, pide al ``LLMProvider`` activo un guion en JSON
estructurado (narración, división en escenas y notas de storyboard), valida ese
JSON con Pydantic y persiste ``Script`` + ``Scene`` en la BD.

El proveedor LLM se resuelve SIEMPRE vía ``registry.resolve("llm", overrides)``:
el motor no conoce Ollama ni OpenAI directamente (invariante de la §3). En el PC
del usuario con Ollama corriendo el guion se genera de verdad; en los tests se
mockea la respuesta del proveedor.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.models.entities import Project, Scene, Script
from app.providers import registry

# Prompt de sistema: fija estructura, tono y duración para que la salida sea
# parseable y consistente escena a escena.
SYSTEM_PROMPT = """\
Eres un guionista de videos cortos narrados. A partir del prompt del usuario,
produces un guion dividido en escenas. Respondes EXCLUSIVAMENTE con un objeto JSON
válido (sin texto adicional, sin markdown) con esta forma exacta:

{
  "structure": "breve descripcion de la estructura narrativa",
  "tone": "tono general (p. ej. inspirador, didactico, epico)",
  "language": "codigo de idioma (p. ej. es)",
  "scenes": [
    {
      "narration_text": "texto de narracion de la escena",
      "visual_prompt": "descripcion visual / consulta de imagen para la escena",
      "duration_s": 5.0,
      "camera_notes": "notas opcionales de camara"
    }
  ]
}

Reglas:
- Entre 3 y 8 escenas, en orden narrativo.
- Cada duration_s es un numero de segundos (entre 2 y 12).
- narration_text y visual_prompt nunca vacios.
- El JSON debe ser estrictamente valido.
"""


# --------------------------------------------------------------------------- #
# Esquemas de validación de la salida del LLM (contrato interno con el modelo)
# --------------------------------------------------------------------------- #


class SceneDraft(BaseModel):
    """Una escena tal como la devuelve el LLM (antes de persistir)."""

    narration_text: str = Field(..., min_length=1)
    visual_prompt: str = Field(..., min_length=1)
    duration_s: float = Field(default=5.0, gt=0)
    camera_notes: str | None = None

    @field_validator("narration_text", "visual_prompt")
    @classmethod
    def _strip_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("no puede estar vacío")
        return value


class ScriptDraft(BaseModel):
    """Guion completo devuelto por el LLM."""

    structure: str | None = None
    tone: str | None = None
    language: str = "es"
    scenes: list[SceneDraft] = Field(..., min_length=1)


class ScriptResult(BaseModel):
    """Resultado de ``generate_script``: el ``Script`` y sus ``Scene`` persistidos."""

    script_id: int
    scene_ids: list[int]


class ScriptEngine:
    """Motor de guion: prompt -> guion validado -> Script/Scene en BD."""

    def generate_script(
        self,
        project: Project,
        session: Session,
        *,
        options: dict | None = None,
    ) -> ScriptResult:
        """Genera y persiste el guion de ``project`` a partir de su prompt.

        ``options`` permite ajustes opcionales (p. ej. ``tone`` sugerido); se pasan
        como pistas al proveedor LLM. Devuelve los ids persistidos.
        """
        options = options or {}
        provider = registry.resolve("llm", project.provider_overrides)

        user_prompt = project.prompt or project.name
        tone_hint = options.get("tone")
        if tone_hint:
            user_prompt = f"{user_prompt}\n\nTono deseado: {tone_hint}"

        raw = provider.complete(
            user_prompt,
            system=SYSTEM_PROMPT,
            json_schema=ScriptDraft.model_json_schema(),
        )
        draft = self._parse(raw)
        return self._persist(project, session, draft, raw)

    # --- helpers ------------------------------------------------------------ #

    @staticmethod
    def _parse(raw: str) -> ScriptDraft:
        """Parsea y valida la respuesta del LLM con Pydantic."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"La respuesta del LLM no es JSON válido: {exc}"
            ) from exc
        try:
            return ScriptDraft.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                f"La respuesta del LLM no cumple el esquema de guion: {exc}"
            ) from exc

    @staticmethod
    def _persist(
        project: Project, session: Session, draft: ScriptDraft, raw: str
    ) -> ScriptResult:
        """Persiste Script + Scene, reemplazando escenas previas del proyecto."""
        # Nueva versión del guion = max previo + 1.
        prev_versions = [s.version for s in project.scripts]
        version = (max(prev_versions) + 1) if prev_versions else 1

        script = Script(
            project_id=project.id,
            structure=draft.structure,
            tone=draft.tone,
            language=draft.language,
            raw_text=raw,
            version=version,
        )
        session.add(script)

        # Regenerar el guion reemplaza las escenas del proyecto.
        for existing in list(project.scenes):
            session.delete(existing)
        session.flush()

        scene_ids: list[int] = []
        for order, scene_draft in enumerate(draft.scenes):
            scene = Scene(
                project_id=project.id,
                order=order,
                narration_text=scene_draft.narration_text,
                visual_prompt=scene_draft.visual_prompt,
                duration_s=scene_draft.duration_s,
                camera_notes=scene_draft.camera_notes,
                status="draft",
            )
            session.add(scene)
            session.flush()
            scene_ids.append(scene.id)

        session.commit()
        return ScriptResult(script_id=script.id, scene_ids=scene_ids)
