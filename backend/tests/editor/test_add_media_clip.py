"""FEAT-007: operación pura ``op_add_media_clip`` (colocar un Asset como clip).

Separa la subida (I/O, validada con Pillow/ffprobe en el endpoint) de la COLOCACIÓN
(operación pura). Cubre:
- Crea el clip del ``kind`` correcto en la pista indicada cuando el ``kind`` casa.
- Para audio/vídeo fija ``source_out_s`` (con ``source_duration_s`` o por defecto
  ``duration_s``); la imagen deja ``source_out_s=None``.
- Rechaza ``kind`` incompatible con la pista, pista inexistente/bloqueada y parámetros
  inválidos (asset_id/start_s/duration_s).
- Queda registrada en ``OPERATIONS``.
"""

from __future__ import annotations

import pytest

from app.engines.editor.model import EditMeta, EditProject, EditTrack
from app.engines.editor.operations import (
    OPERATIONS,
    EditOperationError,
    apply_operation,
)


def _project() -> EditProject:
    return EditProject(
        project_id=1,
        meta=EditMeta(),
        tracks=[
            EditTrack(id="track_video", kind="video", order=0),
            EditTrack(id="track_image", kind="image", order=1),
            EditTrack(id="track_music", kind="music", order=2),
            EditTrack(id="track_locked", kind="voice", order=3, locked=True),
        ],
    )


def test_registered_in_operations():
    assert "add_media_clip" in OPERATIONS


def test_add_video_clip_sets_source_out():
    out = apply_operation(
        _project(),
        "add_media_clip",
        {
            "track_id": "track_video",
            "asset_id": 7,
            "start_s": 2.0,
            "duration_s": 3.5,
            "kind": "video",
            "source_duration_s": 10.0,
        },
    )
    track = out.track_by_id("track_video")
    assert len(track.clips) == 1
    clip = track.clips[0]
    assert clip.kind == "video"
    assert clip.asset_id == 7
    assert clip.start_s == 2.0
    assert clip.duration_s == 3.5
    assert clip.source_in_s == 0.0
    assert clip.source_out_s == 10.0


def test_add_audio_clip_defaults_source_out_to_duration():
    out = apply_operation(
        _project(),
        "add_media_clip",
        {
            "track_id": "track_music",
            "asset_id": 3,
            "start_s": 0.0,
            "duration_s": 4.0,
            "kind": "music",
        },
    )
    clip = out.track_by_id("track_music").clips[0]
    assert clip.kind == "music"
    assert clip.source_out_s == 4.0


def test_add_image_clip_leaves_source_out_none():
    out = apply_operation(
        _project(),
        "add_media_clip",
        {
            "track_id": "track_image",
            "asset_id": 5,
            "start_s": 1.0,
            "duration_s": 2.0,
            "kind": "image",
        },
    )
    clip = out.track_by_id("track_image").clips[0]
    assert clip.kind == "image"
    assert clip.source_out_s is None


def test_rejects_kind_not_matching_track():
    with pytest.raises(EditOperationError, match="pista"):
        apply_operation(
            _project(),
            "add_media_clip",
            {
                "track_id": "track_image",
                "asset_id": 1,
                "start_s": 0.0,
                "duration_s": 1.0,
                "kind": "video",
            },
        )


def test_rejects_unknown_track():
    with pytest.raises(EditOperationError, match="No existe la pista"):
        apply_operation(
            _project(),
            "add_media_clip",
            {"track_id": "nope", "asset_id": 1, "start_s": 0.0, "duration_s": 1.0, "kind": "video"},
        )


def test_rejects_locked_track():
    with pytest.raises(EditOperationError, match="bloqueada"):
        apply_operation(
            _project(),
            "add_media_clip",
            {"track_id": "track_locked", "asset_id": 1, "start_s": 0.0, "duration_s": 1.0, "kind": "voice"},
        )


def test_rejects_missing_asset_id():
    with pytest.raises(EditOperationError, match="asset_id"):
        apply_operation(
            _project(),
            "add_media_clip",
            {"track_id": "track_video", "start_s": 0.0, "duration_s": 1.0, "kind": "video"},
        )


def test_rejects_bad_duration():
    with pytest.raises(EditOperationError, match="duration_s"):
        apply_operation(
            _project(),
            "add_media_clip",
            {"track_id": "track_video", "asset_id": 1, "start_s": 0.0, "duration_s": 0.0, "kind": "video"},
        )


def test_rejects_invalid_kind():
    with pytest.raises(EditOperationError, match="kind"):
        apply_operation(
            _project(),
            "add_media_clip",
            {"track_id": "track_video", "asset_id": 1, "start_s": 0.0, "duration_s": 1.0, "kind": "subtitle"},
        )


def test_deterministic_clip_id_no_collision():
    p = _project()
    p = apply_operation(
        p, "add_media_clip",
        {"track_id": "track_video", "asset_id": 1, "start_s": 0.0, "duration_s": 1.0, "kind": "video"},
    )
    p = apply_operation(
        p, "add_media_clip",
        {"track_id": "track_video", "asset_id": 2, "start_s": 1.0, "duration_s": 1.0, "kind": "video"},
    )
    ids = [c.id for c in p.track_by_id("track_video").clips]
    assert len(ids) == len(set(ids)) == 2
