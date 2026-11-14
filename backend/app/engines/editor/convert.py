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

import json
from pathlib import Path

from app.engines.editor.model import (
    EditClip,
    EditMeta,
    EditProject,
    EditTrack,
    SubtitleStyle,
    SubtitleWord,
    VisualTransform,
)
from app.engines.render.timeline import Timeline
from app.models.entities import Asset, Project

__all__ = ["timeline_to_edit_project"]

# Ganancia de fondo por defecto (coincide con ``RenderEngine._audio_tracks``).
MUSIC_DEFAULT_GAIN = 0.3
SFX_DEFAULT_GAIN = 0.5


def _load_global_words(assets: list[Asset]) -> list[dict]:
    """Lee ``palabras.json`` (``[{text,startMs,endMs}]``) del ``Asset(kind='srt')``.

    Los tiempos de ``palabras.json`` son GLOBALES (acumulados por escena), igual que los
    cues del ``.srt`` del proyecto. Devuelve la lista cruda (ms) o ``[]`` si no hay srt
    con ``words_json`` resoluble. Función de IO acotada: solo lee el fichero apuntado por
    ``meta['words_json']``; el reparto por clip lo hace ``_clip_words`` (puro)."""
    srt = next(
        (
            a
            for a in sorted(assets, key=lambda a: a.id, reverse=True)
            if a.kind == "srt"
        ),
        None,
    )
    if srt is None:
        return []
    words_json = (srt.meta or {}).get("words_json")
    if not words_json:
        return []
    path = Path(words_json)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [w for w in data if isinstance(w, dict) and "text" in w]


def _clip_words(
    global_words: list[dict], clip_start_s: float, clip_end_s: float
) -> list[SubtitleWord]:
    """Selecciona las palabras del cue ``[clip_start_s, clip_end_s]`` (tiempos globales)
    y las convierte a tiempos RELATIVOS al clip (``start_s = startMs/1000 - clip_start_s``).

    Una palabra pertenece al cue si su punto medio cae dentro de la ventana del clip (así
    palabras que solapan el borde se asignan a un único cue de forma determinista). Función
    pura sobre números: testeable sin IO."""
    out: list[SubtitleWord] = []
    for w in global_words:
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        start_ms = w.get("startMs")
        end_ms = w.get("endMs")
        if not isinstance(start_ms, (int, float)) or not isinstance(end_ms, (int, float)):
            continue
        word_start = float(start_ms) / 1000.0
        word_end = float(end_ms) / 1000.0
        mid = (word_start + word_end) / 2.0
        if not (clip_start_s <= mid < clip_end_s):
            continue
        rel_start = max(0.0, word_start - clip_start_s)
        rel_end = max(rel_start, word_end - clip_start_s)
        out.append(
            SubtitleWord(
                text=text,
                start_s=round(rel_start, 3),
                end_s=round(rel_end, 3),
            )
        )
    return out


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
    global_words = _load_global_words(assets)

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
            clip_start = round(offset_s + float(cue.start_s), 3)
            clip_end = clip_start + round(cue_duration, 3)
            words = _clip_words(global_words, clip_start, clip_end)
            subtitle_track.clips.append(
                EditClip(
                    id=f"clip_sub_{clip_index}_{cue_index}",
                    track_id=subtitle_track.id,
                    kind="subtitle",
                    start_s=clip_start,
                    duration_s=round(cue_duration, 3),
                    text=cue.text,
                    style=SubtitleStyle(
                        preset=preset,
                        position="bottom",
                    ),
                    words=words,
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
