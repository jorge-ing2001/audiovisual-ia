"""Mapeo guion -> escena 3D: función pura ``scene_to_spec`` (ver docs/ENGINE-3D-MVP-STYLIZED.md §4).

Convierte una ``Scene`` (narración, ``camera_notes``, ``duration_s``) y su ``Project`` en
un ``Scene3DSpec`` determinista, eligiendo cámara, animación y set desde el catálogo
acotado del MVP con defaults seguros ante texto desconocido. Sin dependencias de Blender.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.engines.engine3d import assets
from app.engines.engine3d.types import CameraSpec, Scene3DSpec
from app.providers.image.aspect import aspect_to_dims

__all__ = ["scene_to_spec", "camera_from_notes", "animation_from_text", "set_from_text"]

# Aspecto por defecto del MVP estilizado: vertical 9:16 -> 1080x1920 (ver §4). Las
# dimensiones reales del clip se derivan de ``project.aspect_ratio`` (igual que el
# timeline, vía ``aspect_to_dims``) para no divergir del lienzo del render.
_DEFAULT_ASPECT = "9:16"

# --- Cámara: palabras clave es/en -> plano / movimiento ------------------------------ #

# Plano (shot). Las claves son subcadenas buscadas en camera_notes en minúsculas.
_SHOT_MAP: list[tuple[tuple[str, ...], str]] = [
    (("primer plano", "primerísimo", "closeup", "close-up", "close up", "rostro", "cara"),
     "closeup"),
    (("plano general", "plano amplio", "wide", "panorámica", "panoramica", "paisaje"),
     "wide"),
    (("aérea", "aerea", "aéreo", "aereo", "aerial", "cenital", "drone", "dron", "desde arriba"),
     "aerial"),
    (("contrapicado", "low angle", "low-angle", "desde abajo", "picado"),
     "low_angle"),
    (("sobre el hombro", "over shoulder", "over-the-shoulder", "over the shoulder", "hombro"),
     "over_shoulder"),
    (("plano medio", "medium", "medio"),
     "medium"),
]

# Movimiento (move).
_MOVE_MAP: list[tuple[tuple[str, ...], str]] = [
    (("zoom in", "zoom-in", "acercamiento", "acercar", "zoom hacia", "zoom al"),
     "zoom_in"),
    (("zoom out", "zoom-out", "alejamiento", "alejar"),
     "zoom_out"),
    (("travelling", "travel", "dolly", "desplazamiento", "travelín", "travelin"),
     "dolly"),
    (("órbita", "orbita", "orbit", "giro alrededor", "rodear", "rodea"),
     "orbit"),
    (("paneo", "panorámica", "panoramica", "pan ", "barrido"),
     "pan"),
    (("estática", "estatica", "estático", "estatico", "static", "fija", "fijo"),
     "static"),
]

# Óptica (focal_mm) por plano: closeup 85mm, medium 50mm, wide 35mm.
_FOCAL_BY_SHOT = {
    "closeup": 85.0,
    "over_shoulder": 85.0,
    "medium": 50.0,
    "low_angle": 50.0,
    "wide": 35.0,
    "aerial": 24.0,
}

# --- Animación: palabras clave es/en -> nombre de animación del catálogo ------------- #

# Orden de prioridad: la primera que coincida gana (más específica antes que idle).
_ANIMATION_MAP: list[tuple[tuple[str, ...], str]] = [
    (("camina", "caminando", "anda", "andando", "corre", "corriendo", "walk", "walking",
      "run", "running", "avanza", "se acerca andando"),
     "walk"),
    (("señala", "señalando", "apunta", "apuntando", "point", "pointing", "indica",
      "muestra con la mano"),
     "point"),
    (("gira la cabeza", "voltea", "mira hacia", "turn head", "turn_head", "gira el rostro",
      "vuelve la cabeza", "mira a"),
     "turn_head"),
    (("reacciona", "reacción", "reaccion", "sorprende", "sorprendido", "asusta", "react",
      "reaction", "se asombra", "se alegra", "gesticula"),
     "react"),
]

# --- Set: palabras clave -> set_id --------------------------------------------------- #

_OUTDOOR_KEYWORDS: tuple[str, ...] = (
    "calle", "exterior", "afuera", "fuera", "ciudad", "street", "outdoor", "outside",
    "avenida", "acera", "plaza", "parque", "carretera",
)


def camera_from_notes(camera_notes: str | None) -> CameraSpec:
    """Mapea ``camera_notes`` (texto libre es/en) a un ``CameraSpec`` con defaults seguros.

    Defaults ante texto desconocido/vacío: plano ``medium``, movimiento ``static``,
    ``focal_mm=50``, ``dof=True`` (ver §5: "valores por defecto seguros").
    """
    text = (camera_notes or "").lower()

    shot = "medium"
    for keywords, value in _SHOT_MAP:
        if any(k in text for k in keywords):
            shot = value
            break

    move = "static"
    for keywords, value in _MOVE_MAP:
        if any(k in text for k in keywords):
            move = value
            break

    focal = _FOCAL_BY_SHOT.get(shot, 50.0)
    return CameraSpec(shot=shot, move=move, focal_mm=focal, dof=True)


def animation_from_text(*texts: str | None) -> str:
    """Elige una animación del catálogo según la narración/``camera_notes``.

    Revisa los textos en orden; la primera palabra clave que coincida fija la animación.
    Por defecto ``idle`` cuando no hay pista (ver §4).
    """
    blob = " ".join(t.lower() for t in texts if t)
    for keywords, value in _ANIMATION_MAP:
        if any(k in blob for k in keywords):
            return value
    return "idle"


def set_from_text(*texts: str | None) -> str:
    """Elige el set por defecto (``room_interior``) salvo que el texto sugiera exterior."""
    blob = " ".join(t.lower() for t in texts if t)
    if any(k in blob for k in _OUTDOOR_KEYWORDS):
        return "outdoor_street"
    return assets.DEFAULT_SET


def _voice_duration_s(scene) -> float | None:
    """Duración de la voz de la escena si está disponible en sus assets (prioritaria).

    Busca el ``Asset(kind='voice')`` MÁS RECIENTE de la escena (mayor ``id``) con
    ``meta['duration_s']`` y devuelve su duración. El criterio "mayor id gana" es el mismo
    que usa ``TimelineBuilder._latest_by_scene`` (ordena por ``id``), de modo que la
    duración que elige el mapeo 3D coincide con la del timeline. Devuelve ``None`` si no
    hay voz todavía (el pipeline 3D sintetiza voz por escena, igual que 2D).
    """
    scene_assets = getattr(scene, "assets", None) or []
    # Orden ascendente por id (el default 0 cubre fakes/objetos sin id persistido);
    # recorremos de menor a mayor para que el de mayor id sea el último que fija ``best``.
    voices = [a for a in scene_assets if getattr(a, "kind", None) == "voice"]
    voices.sort(key=lambda a: (getattr(a, "id", 0) or 0))
    best: float | None = None
    for asset in voices:
        meta = getattr(asset, "meta", None) or {}
        dur = meta.get("duration_s")
        if isinstance(dur, (int, float)) and dur > 0:
            best = float(dur)
    return best


def scene_to_spec(scene, project, *, settings: Settings | None = None) -> Scene3DSpec:
    """Convierte ``Scene`` + ``Project`` en un ``Scene3DSpec`` determinista (función pura).

    Reglas (ver §4):
      - ``duration_s``: duración de la voz si existe, si no ``Scene.duration_s``.
      - ``camera``: ``camera_notes`` -> ``CameraSpec`` (defaults seguros).
      - ``character_animation``: heurística por narración/``camera_notes``.
      - ``set_id``: ``room_interior`` por defecto, ``outdoor_street`` si el texto sugiere exterior.
      - ``style_preset='stylized'``, ``render_engine``/``samples``/``fps`` de settings.
      - ``width``/``height``: derivados de ``project.aspect_ratio`` con ``aspect_to_dims``
        (igual que el timeline); 9:16 -> 1080x1920 en el MVP estilizado. Así el clip y el
        lienzo del render comparten dimensiones (sin desajuste latente fuera de 9:16).
    """
    settings = settings or get_settings()

    aspect = getattr(project, "aspect_ratio", None) or _DEFAULT_ASPECT
    width, height = aspect_to_dims(aspect)

    voice_dur = _voice_duration_s(scene)
    if voice_dur is not None:
        duration_s = voice_dur
    else:
        duration_s = float(getattr(scene, "duration_s", 0.0) or 0.0)

    narration = getattr(scene, "narration_text", "") or ""
    camera_notes = getattr(scene, "camera_notes", None)

    camera = camera_from_notes(camera_notes)
    animation = animation_from_text(narration, camera_notes)
    set_id = set_from_text(narration, camera_notes, getattr(scene, "visual_prompt", ""))

    return Scene3DSpec(
        scene_id=int(getattr(scene, "id", 0) or 0),
        order=int(getattr(scene, "order", 0) or 0),
        duration_s=duration_s,
        character_id=assets.CHARACTER_ID,
        character_animation=animation,
        set_id=set_id,
        camera=camera,
        style_preset="stylized",
        width=width,
        height=height,
        fps=int(settings.fps),
        render_engine=settings.render_engine_3d,
        samples=int(settings.blender_samples),
        seed=None,
    )
