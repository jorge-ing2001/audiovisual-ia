"""Compilador del ``EditProject`` a artefactos de export/preview.

Dos salidas, ambas derivadas del MISMO ``EditProject`` ("lo que ves es lo que exportas"):

- ``compile_remotion_props(edit_project, resolve_asset_src)`` -> dict ``inputProps`` para
  la composición Remotion ``Edit``: pistas visuales (clips con ``from``/``durationInFrames``
  en frames, src del recurso y ``transform``/``transition``) + pista de subtítulos (clips
  con ``text`` + ``style``). Es la contraparte multipista de
  ``Timeline.remotion_input_props``.
- ``compile_audio_tracks(edit_project, resolve_asset_path)`` -> ``list[MixTrack]`` que
  consume el ``AudioMixer`` existente: cada clip de audio aporta un ``MixTrack`` con
  ``delay_s = start_s`` y ``gain`` = volumen efectivo (pista × clip). El recorte
  (``source_in/out``) y los fades se anotan como preparación por clip en
  ``MixTrack.prep`` (lista de filtros FFmpeg aplicados antes de la mezcla).

Ver ``docs/EDITOR-MULTITRACK-ARCHITECTURE.md`` §3.3 y §7.
"""

from __future__ import annotations

from collections.abc import Callable

from app.engines.editor.model import AUDIO_KINDS, VISUAL_KINDS, EditClip, EditProject
from app.engines.voice.mixer import MixTrack

__all__ = ["compile_remotion_props", "compile_audio_tracks"]


def _frame_at(seconds: float, fps: int) -> int:
    """Frame de inicio (posición ``from``) para un instante en segundos (>= 0)."""
    return max(0, round(seconds * fps))


def _frames_len(seconds: float, fps: int) -> int:
    """Longitud en frames de una duración (al menos 1)."""
    return max(1, round(seconds * fps))


def _visual_clip_props(
    clip: EditClip, fps: int, resolve_src: Callable[[int | None], str | None]
) -> dict:
    transform = clip.transform
    transform_props = None
    if transform is not None:
        transform_props = {
            "scale": transform.scale,
            "x": transform.x,
            "y": transform.y,
            "rotation": transform.rotation,
            "crop": {
                "l": transform.crop_l,
                "t": transform.crop_t,
                "r": transform.crop_r,
                "b": transform.crop_b,
            },
            "fit": transform.fit,
        }

    def _transition(t):
        if t is None:
            return None
        return {"type": t.type, "durationInFrames": _frames_len(t.duration_s, fps)}

    return {
        "id": clip.id,
        "kind": clip.kind,
        "from": _frame_at(clip.start_s, fps),
        "durationInFrames": _frames_len(clip.duration_s, fps),
        "startS": round(clip.start_s, 3),
        "durationS": round(clip.duration_s, 3),
        "src": resolve_src(clip.asset_id),
        "assetId": clip.asset_id,
        "transform": transform_props,
        "transitionIn": _transition(clip.transition_in),
        "transitionOut": _transition(clip.transition_out),
        "effects": [{"type": e.type, "params": e.params} for e in clip.effects],
    }


def _subtitle_clip_props(clip: EditClip, fps: int) -> dict:
    style = clip.style
    style_props = None
    if style is not None:
        style_props = {
            "fontFamily": style.font_family,
            "fontSize": style.font_size,
            "color": style.color,
            "bgColor": style.bg_color,
            "bold": style.bold,
            "outline": style.outline,
            "position": style.position,
            "x": style.x,
            "y": style.y,
            "preset": style.preset,
        }
    return {
        "id": clip.id,
        "from": _frame_at(clip.start_s, fps),
        "durationInFrames": _frames_len(clip.duration_s, fps),
        "startS": round(clip.start_s, 3),
        "durationS": round(clip.duration_s, 3),
        "text": clip.text or "",
        "style": style_props,
    }


def compile_remotion_props(
    edit_project: EditProject,
    resolve_asset_src: Callable[[int | None], str | None] | None = None,
) -> dict:
    """Compila el ``EditProject`` a ``inputProps`` de la composición ``Edit``.

    ``resolve_asset_src`` traduce un ``asset_id`` a la ``src`` que la composición usará
    (ruta relativa al ``publicDir`` para headless, o URL para el Player). Si no se pasa,
    se emite ``None`` (útil en tests de forma).
    """
    resolve = resolve_asset_src or (lambda _asset_id: None)
    fps = edit_project.meta.fps

    visual_clips: list[dict] = []
    subtitle_clips: list[dict] = []
    for track in sorted(edit_project.tracks, key=lambda t: t.order):
        for clip in track.clips:
            if not clip.enabled:
                continue
            if clip.kind in VISUAL_KINDS:
                props = _visual_clip_props(clip, fps, resolve)
                props["trackId"] = track.id
                props["trackOrder"] = track.order
                visual_clips.append(props)
            elif clip.kind == "subtitle":
                subtitle_clips.append(_subtitle_clip_props(clip, fps))

    # Orden estable por posición temporal (y luego por z/orden de pista para visuales).
    visual_clips.sort(key=lambda c: (c["from"], c["trackOrder"]))
    subtitle_clips.sort(key=lambda c: c["from"])

    total_frames = _frames_len(edit_project.duration_s, fps) if edit_project.duration_s > 0 else 1

    return {
        "fps": fps,
        "width": edit_project.meta.width,
        "height": edit_project.meta.height,
        "durationInFrames": total_frames,
        "visualClips": visual_clips,
        "subtitleClips": subtitle_clips,
    }


def compile_audio_tracks(
    edit_project: EditProject,
    resolve_asset_path: Callable[[int | None], str | None],
) -> list[MixTrack]:
    """Compila las pistas de audio del ``EditProject`` a ``MixTrack`` del ``AudioMixer``.

    Cada clip de audio habilitado (y de pista no muteada) con un ``asset_id`` resoluble
    aporta un ``MixTrack`` con ``delay_s = start_s`` y ``gain`` efectivo (pista × clip).
    El recorte (``source_in/out``) y los fades se anotan en ``MixTrack.prep`` como pasos
    de preparación FFmpeg por clip (``atrim`` + ``afade``), sin tocar disco aquí.
    """
    tracks: list[MixTrack] = []
    for track in sorted(edit_project.tracks, key=lambda t: t.order):
        if track.kind not in AUDIO_KINDS or track.muted:
            continue
        for clip in track.clips:
            if not clip.enabled:
                continue
            path = resolve_asset_path(clip.asset_id)
            if not path:
                continue
            effective_gain = round(track.volume * clip.gain, 6)
            prep = _clip_prep_filters(clip)
            tracks.append(
                MixTrack(
                    path=path,
                    gain=effective_gain,
                    delay_s=round(clip.start_s, 3),
                    prep=prep,
                )
            )
    return tracks


def _clip_prep_filters(clip: EditClip) -> list[str]:
    """Filtros FFmpeg de preparación por clip (recorte + fades), antes de la mezcla.

    - ``atrim`` recorta el origen a ``[source_in_s, source_out_s]`` (si hay recorte).
    - ``afade`` aplica fade-in/out sobre el clip ya recortado.
    """
    filters: list[str] = []
    if clip.source_in_s > 0 or clip.source_out_s is not None:
        parts = [f"start={round(clip.source_in_s, 3)}"]
        if clip.source_out_s is not None:
            parts.append(f"end={round(clip.source_out_s, 3)}")
        filters.append("atrim=" + ":".join(parts))
        # Tras recortar hay que reponer la base de tiempo a 0 para que ``adelay`` posicione
        # el clip correctamente en la mezcla.
        filters.append("asetpts=PTS-STARTPTS")
    if clip.fade_in_s > 0:
        filters.append(f"afade=t=in:st=0:d={round(clip.fade_in_s, 3)}")
    if clip.fade_out_s > 0:
        fade_start = max(0.0, clip.duration_s - clip.fade_out_s)
        filters.append(f"afade=t=out:st={round(fade_start, 3)}:d={round(clip.fade_out_s, 3)}")
    return filters
