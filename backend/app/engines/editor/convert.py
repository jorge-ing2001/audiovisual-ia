"""Conversión ``Timeline`` (por escena) → ``EditProject`` (multipista).

Al abrir el editor se deriva un ``EditProject`` del ``Timeline`` de generación (que es
por escena). Es determinista y testeable: dado un ``Timeline`` + los ``Asset`` del
proyecto, produce las pistas video/image, voice, music, sfx y subtitle con los offsets
acumulados correctos. Ver ``docs/EDITOR-MULTITRACK-ARCHITECTURE.md`` §3.3.

Decisiones (preservan el comportamiento actual del render como punto de partida):
- Por cada ``TimelineScene`` se crea un clip visual (video si hay ``video_src``, si no
  image) en ``start_s`` = offset acumulado y ``duration_s`` = duración de la escena.
- Si la escena tiene voz (``Asset(kind='voice')`` de esa escena) se crea un clip en la
  pista ``voice`` en ese mismo offset, con ``duration_s`` = duración real de la voz
  (``Asset.meta['duration_s']`` o la duración de la escena) y ``gain=1.0``.
- Cada ``SubtitleCue`` de la escena produce un clip en la pista ``subtitle`` en
  ``start_s`` = offset_escena + ``cue.start_s`` y ``duration_s`` = ``cue.end_s -
  cue.start_s``, con ``text`` = ``cue.text`` y ``style.preset`` derivado de
  ``subtitle_style``.
- La música/SFX a nivel de proyecto (``Asset(kind='music'|'sfx')``) se añaden como clips
  de fondo desde ``start_s=0`` con el gain por defecto del mixer (música 0.3, SFX 0.5).
"""

from __future__ import annotations

from pathlib import Path

from app.engines.editor.model import (
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
    SubtitleStyle,
    VisualTransform,
)
from app.engines.render.timeline import Timeline
from app.models.entities import Asset, Project

__all__ = ["timeline_to_edit_project"]

# Ganancia de fondo por defecto (coincide con ``RenderEngine._audio_tracks``).
MUSIC_DEFAULT_GAIN = 0.3
SFX_DEFAULT_GAIN = 0.5


def _timeline_scene_db_id(timeline_scene_id: str) -> int | None:
    """Extrae el id de escena en BD del id del timeline (``scene_{order}_{db_id}``)."""
    parts = str(timeline_scene_id).split("_")
    if len(parts) < 3:
        return None
    try:
        return int(parts[-1])
    except ValueError:
        return None


def _voice_by_scene(assets: list[Asset]) -> dict[int, Asset]:
    out: dict[int, Asset] = {}
    for asset in sorted(assets, key=lambda a: a.id):
        if asset.kind == "voice" and asset.scene_id is not None:
            out[asset.scene_id] = asset
    return out


def timeline_to_edit_project(
    timeline: Timeline,
    project: Project,
    assets: list[Asset],
) -> EditProject:
    """Deriva un ``EditProject`` multipista desde un ``Timeline`` por escena.

    ``assets`` son los ``Asset`` del proyecto (para resolver voz por escena y las pistas
    de música/SFX de fondo). La conversión es pura: no toca disco ni render.
    """
    meta = EditMeta(
        fps=timeline.fps,
        width=timeline.width,
        height=timeline.height,
        aspect_ratio=timeline.aspect_ratio,
    )

    # Pistas (orden visible de arriba a abajo en el editor).
    video_track = EditTrack(id="track_video", kind="video", name="Vídeo", order=0)
    image_track = EditTrack(id="track_image", kind="image", name="Imagen", order=1)
    voice_track = EditTrack(id="track_voice", kind="voice", name="Voz", order=2)
    music_track = EditTrack(id="track_music", kind="music", name="Música", order=3)
    sfx_track = EditTrack(id="track_sfx", kind="sfx", name="SFX", order=4)
    subtitle_track = EditTrack(
        id="track_subtitle", kind="subtitle", name="Subtítulos", order=5
    )

    voice_map = _voice_by_scene(assets)

    offset_s = 0.0
    clip_index = 0
    for tscene in timeline.scenes:
        scene_duration = float(tscene.duration_s)
        db_id = _timeline_scene_db_id(tscene.id)

        # Clip visual: vídeo si existe, si no imagen.
        if tscene.video_src:
            video_track.clips.append(
                EditClip(
                    id=f"clip_visual_{clip_index}",
                    track_id=video_track.id,
                    kind="video",
                    start_s=round(offset_s, 3),
                    duration_s=round(scene_duration, 3),
                    source_in_s=0.0,
                    source_out_s=round(scene_duration, 3),
                    asset_id=db_id,
                    transform=VisualTransform(fit="cover"),
                )
            )
        elif tscene.image_src:
            image_track.clips.append(
                EditClip(
                    id=f"clip_visual_{clip_index}",
                    track_id=image_track.id,
                    kind="image",
                    start_s=round(offset_s, 3),
                    duration_s=round(scene_duration, 3),
                    source_in_s=0.0,
                    source_out_s=round(scene_duration, 3),
                    asset_id=db_id,
                    transform=VisualTransform(fit="cover"),
                )
            )

        # Clip de voz de la escena.
        voice_asset = voice_map.get(db_id) if db_id is not None else None
        if voice_asset is not None:
            voice_duration = float((voice_asset.meta or {}).get("duration_s", 0.0))
            if voice_duration <= 0:
                voice_duration = scene_duration
            voice_track.clips.append(
                EditClip(
                    id=f"clip_voice_{clip_index}",
                    track_id=voice_track.id,
                    kind="voice",
                    start_s=round(offset_s, 3),
                    duration_s=round(voice_duration, 3),
                    source_in_s=0.0,
                    source_out_s=round(voice_duration, 3),
                    asset_id=voice_asset.id,
                    gain=1.0,
                )
            )

        # Subtítulos de la escena (cues locales → tiempos absolutos).
        preset = "tiktok" if tscene.subtitle_style == "tiktok" else "classic"
        for cue_index, cue in enumerate(tscene.cues):
            cue_duration = float(cue.end_s) - float(cue.start_s)
            if cue_duration <= 0:
                continue
            subtitle_track.clips.append(
                EditClip(
                    id=f"clip_sub_{clip_index}_{cue_index}",
                    track_id=subtitle_track.id,
                    kind="subtitle",
                    start_s=round(offset_s + float(cue.start_s), 3),
                    duration_s=round(cue_duration, 3),
                    text=cue.text,
                    style=SubtitleStyle(
                        preset=preset,
                        position="bottom",
                    ),
                )
            )

        offset_s += scene_duration
        clip_index += 1

    # Música / SFX de proyecto como clips de fondo desde t=0.
    bg_index = 0
    for asset in sorted(assets, key=lambda a: a.id):
        if asset.kind == "music":
            music_track.clips.append(
                EditClip(
                    id=f"clip_music_{bg_index}",
                    track_id=music_track.id,
                    kind="music",
                    start_s=0.0,
                    duration_s=round(offset_s, 3),
                    asset_id=asset.id,
                    gain=MUSIC_DEFAULT_GAIN,
                )
            )
            bg_index += 1
        elif asset.kind == "sfx":
            sfx_track.clips.append(
                EditClip(
                    id=f"clip_sfx_{bg_index}",
                    track_id=sfx_track.id,
                    kind="sfx",
                    start_s=0.0,
                    duration_s=round(offset_s, 3),
                    asset_id=asset.id,
                    gain=SFX_DEFAULT_GAIN,
                )
            )
            bg_index += 1

    source_version = max(
        (t.version for t in getattr(project, "timelines", [])), default=None
    )

    return EditProject(
        project_id=project.id,
        meta=meta,
        tracks=[
            video_track,
            image_track,
            voice_track,
            music_track,
            sfx_track,
            subtitle_track,
        ],
        source_timeline_version=source_version,
    )


def _asset_path(asset: Asset | None) -> str | None:
    """Ruta en disco de un asset (o ``None``)."""
    if asset is None or not asset.path:
        return None
    return str(Path(asset.path))
