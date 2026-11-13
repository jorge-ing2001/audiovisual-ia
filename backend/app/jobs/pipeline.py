"""Pipeline end-to-end prompt->video: handler de job ``generate_video`` (FEAT-006).

Este módulo orquesta los motores ya construidos (FEAT-003..005) en un único job de la
cola existente, siguiendo el pipeline de ``docs/PHASE-1-MVP.md``:

    prompt -> [IA] guion -> escenas
           -> [Visual 2D] imagen por escena (stock/placeholder)
           -> [Voz] TTS por escena -> música/SFX (opcional) -> mezcla -14 LUFS
           -> [Subtítulos] faster-whisper -> SRT
           -> [Timeline] JSON -> [Render] Remotion -> FFmpeg mux -> MP4

Invariantes (prompt maestro §8/§9):
  - No reimplementa motores: los encadena. Todos los proveedores se resuelven vía el
    registry (modo local/gratuito por defecto).
  - Sub-pasos REANUDABLES e idempotentes: antes de regenerar la imagen/voz de una escena
    consulta la cache de ``Asset``; si ya existe un asset válido (fichero en disco) se
    salta. Un reintento del job NO rehace el trabajo ya hecho de otras escenas.
  - Reintentos por escena con backoff SIN reiniciar todo el pipeline (``_with_retries``).
  - Progreso por paso persistido en la tabla ``Job`` (``progress`` + desglose en
    ``Job.result['steps']`` / ``Job.result['current']``).
  - Nada simulado presentado como real: si Ollama no está y no se inyecta un guion
    fixture etiquetado, el paso de guion falla con un mensaje accionable.

El handler se registra como tipo de job ``generate_video`` al importar este módulo
(lo hace ``app.jobs.handlers``), y el worker lo reclama igual que el handler ``render``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from app.engines.render import RenderEngine, RenderUnavailable
from app.engines.render.engine import FfmpegError
from app.engines.script import ScriptEngine
from app.engines.script.engine import ScriptDraft
from app.engines.visual2d import VisualEngine
from app.engines.voice import VoiceEngine
from app.jobs.queue import JobQueue
from app.models.entities import Asset, Job, Project, Scene, Script
from app.providers import registry
from app.storage.db import session_scope

__all__ = ["generate_video_handler", "PipelineError", "ScriptSourceError"]

# Pasos del pipeline, en orden, con el peso (porcentaje acumulado) que representa cada
# uno sobre el total. Permite reportar un ``progress`` monótono y legible en el Job.
STEP_SCRIPT = "guion"
STEP_SCENES = "escenas"  # imagen + voz por escena (el grueso del trabajo)
STEP_AUDIO = "musica_sfx"
STEP_SUBTITLES = "subtitulos"
STEP_TIMELINE = "timeline"
STEP_RENDER = "render"

# Reintentos por sub-paso de escena (imagen/voz) con backoff exponencial.
SCENE_MAX_RETRIES = 2
SCENE_BASE_BACKOFF_S = 0.0  # 0 en tests; el worker real puede subirlo vía payload.


class PipelineError(RuntimeError):
    """Fallo irrecuperable del pipeline (se propaga al worker para failed/reintento)."""


class ScriptSourceError(PipelineError):
    """No hay forma de obtener un guion: Ollama no disponible y sin fixture.

    Mensaje accionable (§9): nunca se inventa un guion y se presenta como IA.
    """


# --------------------------------------------------------------------------- #
# Reporte de progreso en la tabla Job
# --------------------------------------------------------------------------- #


@dataclass
class _Progress:
    """Acumula el estado por paso y lo persiste en el Job (``progress`` + ``result``)."""

    job_id: int
    steps: list[dict] = field(default_factory=list)
    current: dict | None = None

    def start(self, step: str, *, total: int | None = None, detail: str = "") -> None:
        self.current = {"step": step, "detail": detail, "done": 0, "total": total}
        self._flush(progress=self._progress_for(step, 0, total))

    def advance(self, step: str, *, done: int, total: int | None, detail: str = "") -> None:
        self.current = {"step": step, "detail": detail, "done": done, "total": total}
        self._flush(progress=self._progress_for(step, done, total))

    def finish(self, step: str, detail: str = "") -> None:
        self.steps.append({"step": step, "detail": detail, "status": "done"})
        self.current = None
        self._flush(progress=_STEP_END.get(step, 0.0))

    # --- helpers -------------------------------------------------------------
    @staticmethod
    def _progress_for(step: str, done: int, total: int | None) -> float:
        start = _STEP_START.get(step, 0.0)
        end = _STEP_END.get(step, start)
        if not total:
            return start
        frac = max(0.0, min(1.0, done / total))
        return round(start + (end - start) * frac, 4)

    def _flush(self, *, progress: float) -> None:
        with session_scope() as session:
            job = session.get(Job, self.job_id)
            if job is None:  # pragma: no cover - el job existe mientras corre
                return
            job.progress = max(float(job.progress or 0.0), progress)
            # El result in-progress refleja el desglose por paso (consultable vía GET /jobs/{id}).
            result = dict(job.result or {})
            result["steps"] = list(self.steps)
            result["current"] = self.current
            job.result = result


# Tramos de ``progress`` (0..1) por paso: la suma cubre el pipeline completo.
_STEP_START = {
    STEP_SCRIPT: 0.0,
    STEP_SCENES: 0.1,
    STEP_AUDIO: 0.7,
    STEP_SUBTITLES: 0.75,
    STEP_TIMELINE: 0.85,
    STEP_RENDER: 0.9,
}
_STEP_END = {
    STEP_SCRIPT: 0.1,
    STEP_SCENES: 0.7,
    STEP_AUDIO: 0.75,
    STEP_SUBTITLES: 0.85,
    STEP_TIMELINE: 0.9,
    STEP_RENDER: 1.0,
}


def _with_retries(fn, *, what: str, max_retries: int = SCENE_MAX_RETRIES,
                  base_backoff: float = SCENE_BASE_BACKOFF_S):
    """Ejecuta ``fn`` reintentando con backoff exponencial ante excepción.

    Se usa para los sub-pasos por escena (imagen/voz): un fallo transitorio de una escena
    se reintenta sin reiniciar todo el pipeline. Si se agotan los reintentos se propaga
    la última excepción (el worker la convierte en failed/reintento del job completo).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - se reintenta el sub-paso aislado
            last_exc = exc
            if attempt < max_retries and base_backoff > 0:
                time.sleep(base_backoff * (2**attempt))
    raise PipelineError(f"{what} falló tras {max_retries + 1} intentos: {last_exc}") from last_exc


# --------------------------------------------------------------------------- #
# Paso 1: guion (ScriptEngine o fixture etiquetado)
# --------------------------------------------------------------------------- #

# Marca que etiqueta un guion como FIXTURE de test/verificación (nunca salida real de IA).
FIXTURE_SOURCE = "fixture"


def _ollama_reachable(overrides: dict | None) -> bool:
    """Comprueba si el servidor Ollama responde (ping ligero a ``/api/tags``)."""
    if registry.active_backend("llm", overrides) != "ollama":
        # Otro backend LLM (p. ej. openai): dejamos que el motor lo intente/falle.
        return True
    from app.config import get_settings

    url = get_settings().ollama_url.rstrip("/")
    try:
        resp = httpx.get(f"{url}/api/tags", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


def _persist_fixture_script(project: Project, session: Session, fixture: dict) -> None:
    """Persiste un guion FIXTURE (etiquetado) como Script + Scenes, sin llamar al LLM.

    El fixture se valida con el mismo esquema que la salida del LLM (``ScriptDraft``) para
    garantizar que es un guion bien formado, pero el ``Script`` resultante queda marcado
    con ``source=fixture`` en su ``raw_text`` (JSON) para que NUNCA se confunda con una
    generación real de IA.
    """
    import json

    draft = ScriptDraft.model_validate(fixture)

    prev_versions = [s.version for s in project.scripts]
    version = (max(prev_versions) + 1) if prev_versions else 1

    # raw_text lleva el marcador explícito de origen (auditable; no es salida de IA).
    raw_payload = {"source": FIXTURE_SOURCE, "script": draft.model_dump(mode="json")}
    script = Script(
        project_id=project.id,
        structure=f"[{FIXTURE_SOURCE}] {draft.structure or ''}".strip(),
        tone=draft.tone,
        language=draft.language,
        raw_text=json.dumps(raw_payload, ensure_ascii=False),
        version=version,
    )
    session.add(script)
    for existing in list(project.scenes):
        session.delete(existing)
    session.flush()
    for order, scene_draft in enumerate(draft.scenes):
        session.add(
            Scene(
                project_id=project.id,
                order=order,
                narration_text=scene_draft.narration_text,
                visual_prompt=scene_draft.visual_prompt,
                duration_s=scene_draft.duration_s,
                camera_notes=scene_draft.camera_notes,
                status="draft",
            )
        )
    session.commit()


def _ensure_script(project: Project, session: Session, options: dict, progress: _Progress) -> str:
    """Garantiza que el proyecto tiene Script + Scenes. Devuelve el origen ('llm'|'fixture').

    Reanudable: si ya hay escenas (de una ejecución previa del job) no regenera el guion.
    Si no las hay: usa el fixture etiquetado si se inyectó; si no, exige Ollama disponible
    y falla con mensaje accionable en caso contrario.
    """
    progress.start(STEP_SCRIPT, detail="preparando guion")

    if project.scenes:
        progress.finish(STEP_SCRIPT, detail=f"guion existente ({len(project.scenes)} escenas)")
        return "existing"

    fixture = options.get("script_fixture")
    if fixture:
        _persist_fixture_script(project, session, fixture)
        session.refresh(project)
        progress.finish(
            STEP_SCRIPT,
            detail=(
                f"guion FIXTURE etiquetado ({len(project.scenes)} escenas, "
                f"source={FIXTURE_SOURCE})"
            ),
        )
        return FIXTURE_SOURCE

    if not _ollama_reachable(project.provider_overrides):
        from app.config import get_settings

        raise ScriptSourceError(
            "El paso de guion no puede ejecutarse: Ollama no está disponible en "
            f"{get_settings().ollama_url} (AIV_OLLAMA_URL). Inicia Ollama con el modelo "
            "configurado, o inyecta un guion fixture etiquetado "
            "(options.script_fixture en POST /generate) para verificar el resto del pipeline."
        )

    ScriptEngine().generate_script(project, session, options=options.get("script_options"))
    session.refresh(project)
    progress.finish(STEP_SCRIPT, detail=f"guion IA ({len(project.scenes)} escenas)")
    return "llm"


# --------------------------------------------------------------------------- #
# Paso 2: imagen + voz por escena (reanudable, con cache)
# --------------------------------------------------------------------------- #


def _scene_has_valid_image(session: Session, scene: Scene) -> bool:
    """¿La escena ya tiene un Asset de imagen con fichero en disco? (idempotencia)."""
    from pathlib import Path

    for asset in scene.assets:
        if asset.kind == "image" and Path(asset.path).is_file():
            return True
    return False


def _scene_has_valid_voice(session: Session, scene: Scene) -> bool:
    """¿La escena ya tiene un Asset de voz con fichero en disco? (idempotencia)."""
    from pathlib import Path

    for asset in scene.assets:
        if asset.kind == "voice" and Path(asset.path).is_file():
            return True
    return False


def _generate_scene_assets(
    project: Project, session: Session, options: dict, progress: _Progress
) -> None:
    """Genera (o reutiliza de cache) imagen y voz de cada escena, de forma reanudable.

    - Imagen: ``VisualEngine.render_scene_image`` ya cachea por hash; además saltamos la
      escena si ya tiene una imagen válida en disco.
    - Voz: se sintetiza por escena para poder reanudar escena a escena (``VoiceEngine``
      sintetiza y persiste solo las escenas sin voz válida).
    - Cada escena se intenta con reintentos/backoff sin perder el trabajo de las demás.
    """
    scenes = sorted(project.scenes, key=lambda s: (s.order, s.id))
    total = len(scenes)
    style = options.get("style")

    visual = VisualEngine()
    for index, scene in enumerate(scenes):
        progress.advance(
            STEP_SCENES, done=index, total=total,
            detail=f"escena {index + 1}/{total}: imagen+voz",
        )
        # --- imagen (idempotente) ---
        if not _scene_has_valid_image(session, scene):
            _with_retries(
                lambda s=scene: visual.render_scene_image(s, session, style=style),
                what=f"imagen de la escena {scene.id}",
            )
            session.refresh(scene)
        # --- voz (idempotente) ---
        if not _scene_has_valid_voice(session, scene):
            _with_retries(
                lambda s=scene: _synthesize_scene_voice(project, s, session),
                what=f"voz de la escena {scene.id}",
            )
            session.refresh(scene)

    progress.advance(STEP_SCENES, done=total, total=total, detail="imágenes y voces listas")
    progress.finish(STEP_SCENES, detail=f"{total} escenas con imagen y voz")


def _synthesize_scene_voice(project: Project, scene: Scene, session: Session) -> None:
    """Sintetiza la voz de UNA escena y persiste su ``Asset(kind='voice')``.

    Reutiliza ``VoiceEngine.synthesize`` (con la misma degradación kokoro->edge) pero a
    nivel de escena para que el pipeline sea reanudable escena a escena. Las escenas sin
    narración simplemente no producen voz.
    """
    from app.providers.base import VoiceLine
    from app.storage.media import ensure_project_workspace

    text = (scene.narration_text or "").strip()
    if not text:
        return
    language = project.scripts[-1].language if project.scripts else "es"
    out_dir = ensure_project_workspace(project.id) / "voice" / f"scene_{scene.id}"
    engine = VoiceEngine()
    clips = engine.synthesize(
        [VoiceLine(text=text, language=language)],
        str(out_dir),
        project_overrides=project.provider_overrides,
    )
    if not clips:
        return
    clip = clips[0]
    asset = Asset(
        project_id=project.id,
        scene_id=scene.id,
        kind="voice",
        path=clip.path,
        provider=clip.provider,
        meta={
            "duration_s": clip.duration_s,
            "sample_rate": clip.sample_rate,
            **(clip.meta or {}),
        },
    )
    session.add(asset)
    session.commit()


# --------------------------------------------------------------------------- #
# Handler del job
# --------------------------------------------------------------------------- #


def generate_video_handler(payload: dict | None, job_id: int) -> dict:
    """Handler del job ``generate_video``: encadena el pipeline prompt->MP4.

    ``payload`` admite:
      - ``project_id`` (obligatorio): proyecto a producir.
      - ``options`` (opcional):
          - ``script_fixture``: guion fixture ETIQUETADO (dict con la forma de ``ScriptDraft``)
            para verificar el pipeline sin Ollama. Nunca se presenta como salida real de IA.
          - ``script_options``: pistas para el ScriptEngine cuando SÍ hay LLM.
          - ``style``: guía de estilo visual para las imágenes.

    Devuelve el ``asset_id`` del vídeo, su ruta y metadatos. Propaga ``RenderUnavailable``
    si el render no puede ejecutarse aquí (no se simula el MP4).
    """
    payload = payload or {}
    project_id = payload.get("project_id")
    if project_id is None:
        raise PipelineError("El job 'generate_video' requiere 'project_id' en el payload.")
    options = payload.get("options") or {}

    progress = _Progress(job_id=job_id)

    with session_scope() as session:
        project = session.get(Project, int(project_id))
        if project is None:
            raise PipelineError(f"Proyecto {project_id} no encontrado para el pipeline.")

        # Validación de prompt: sin prompt ni guion previo ni fixture no hay nada que producir.
        has_prompt = bool((project.prompt or "").strip())
        if not project.scenes and not options.get("script_fixture") and not has_prompt:
            raise PipelineError(
                "El proyecto no tiene prompt ni guion; define un prompt o inyecta "
                "options.script_fixture antes de generar el vídeo."
            )

        # 1) Guion (IA o fixture etiquetado).
        script_source = _ensure_script(project, session, options, progress)

        # 2) Imagen + voz por escena (reanudable, con cache y reintentos por escena).
        _generate_scene_assets(project, session, options, progress)

        # 3) Música / SFX de proyecto (opcional): registrar recursos provistos.
        progress.start(STEP_AUDIO, detail="música/SFX")
        _register_project_audio(project, session, options)
        progress.finish(STEP_AUDIO, detail="pista de audio preparada")

        # 4) Subtítulos: ASR real si está disponible; si no, derivados del texto de la
        #    narración por escena (determinista, etiquetado 'text-derived'). Siempre se
        #    generan mientras haya voz + texto, para que se quemen en el MP4.
        progress.start(STEP_SUBTITLES, detail="subtítulos (ASR o derivados del texto)")
        session.refresh(project)
        subtitle_asset = VoiceEngine().generate_subtitles(project, session)
        if subtitle_asset is not None:
            detail = (
                f"subtítulos generados (fuente={(subtitle_asset.meta or {}).get('source')})"
            )
        else:
            detail = "sin voz/texto: no hay subtítulos que generar"
        progress.finish(STEP_SUBTITLES, detail=detail)

        # 5) + 6) Timeline + render Remotion + mux FFmpeg -> MP4 (RenderEngine.compose).
        progress.start(STEP_TIMELINE, detail="ensamblando timeline")
        session.refresh(project)
        try:
            result = RenderEngine().compose(project, session)
        except RenderUnavailable:
            # No se puede renderizar aquí: se propaga tal cual (el worker lo marca failed/
            # reintenta). Nunca se simula el MP4.
            raise
        except FfmpegError as exc:
            raise PipelineError(f"El mux de FFmpeg falló: {exc}") from exc
        progress.finish(STEP_RENDER, detail="MP4 final producido")

        return {
            "project_id": int(project_id),
            "script_source": script_source,
            "asset_id": result.asset_id,
            "video_path": result.video_path,
            "timeline_path": result.timeline_path,
            "duration_s": result.meta.get("duration_s"),
            "subtitles": bool(subtitle_asset),
            "steps": list(progress.steps),
        }


def _register_project_audio(project: Project, session: Session, options: dict) -> None:
    """Registra música/SFX de proyecto si el payload provee rutas de recursos (opcional).

    El MVP local/gratuito no sintetiza música; si el usuario provee ficheros (recursos
    MIT libres) se enlazan como ``Asset(kind='music'|'sfx')`` para la mezcla/timeline.
    Idempotente: no re-registra un recurso cuyo path ya existe como asset del proyecto.
    """
    tracks = options.get("audio_tracks") or []
    if not tracks:
        return
    existing_paths = {a.path for a in project.assets if a.kind in ("music", "sfx")}
    engine = VoiceEngine()
    for track in tracks:
        kind = track.get("kind")
        path = track.get("path")
        if kind not in ("music", "sfx") or not path or path in existing_paths:
            continue
        engine.register_audio_asset(
            project, session, kind=kind, path=path,
            provider=track.get("provider", "local"), meta=track.get("meta"),
        )
    session.refresh(project)


def enqueue_generate_video(
    project_id: int, *, options: dict | None = None, max_retries: int = 1
) -> int:
    """Encola un job ``generate_video`` para un proyecto y devuelve su id.

    ``max_retries`` bajo por defecto: el pipeline ya reintenta por escena internamente;
    un reintento del job completo es reanudable (aprovecha la cache de assets).
    """
    return JobQueue().enqueue(
        "generate_video",
        project_id=project_id,
        payload={"project_id": project_id, "options": options or {}},
        max_retries=max_retries,
    )
