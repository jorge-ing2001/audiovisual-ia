"""FEAT-003: marcadores de la línea de tiempo (puros, sin render).

Cubre:
- ``op_add_marker``/``op_remove_marker``/``op_set_marker`` gestionan ``markers[]`` y quedan
  registradas en ``OPERATIONS``; casos inválidos lanzan ``EditOperationError``.
- Los marcadores son METADATOS: el compilador NO los emite a ``inputProps`` y un
  ``EditProject`` sin ``markers`` produce EXACTAMENTE el mismo ``inputProps`` que con ellos
  (regresión §3.4).
"""

from __future__ import annotations

import pytest

from app.engines.editor.compile import compile_remotion_props
from app.engines.editor.model import (
    EditClip,
    EditMarker,
    EditMeta,
    EditProject,
    EditTrack,
    SubtitleStyle,
)
from app.engines.editor.operations import (
    OPERATIONS,
    EditOperationError,
    apply_operation,
)


def _project() -> EditProject:
    image = EditTrack(
        id="track_image",
        kind="image",
        order=0,
        clips=[
            EditClip(
                id="v0",
                track_id="track_image",
                kind="image",
                start_s=0.0,
                duration_s=2.0,
                asset_id=1,
            ),
            EditClip(
                id="v1",
                track_id="track_image",
                kind="image",
                start_s=2.0,
                duration_s=3.0,
                asset_id=2,
            ),
        ],
    )
    subs = EditTrack(
        id="track_subtitle",
        kind="subtitle",
        order=1,
        clips=[
            EditClip(
                id="s0",
                track_id="track_subtitle",
                kind="subtitle",
                start_s=0.0,
                duration_s=2.0,
                text="hola",
                style=SubtitleStyle(),
            )
        ],
    )
    return EditProject(project_id=1, meta=EditMeta(fps=30), tracks=[image, subs])


# --------------------------------------------------------------------------- #
# Registro + modelo
# --------------------------------------------------------------------------- #


def test_marker_ops_registered():
    assert "add_marker" in OPERATIONS
    assert "remove_marker" in OPERATIONS
    assert "set_marker" in OPERATIONS


def test_project_defaults_to_empty_markers():
    assert _project().markers == []


def test_marker_rejects_negative_t_s_in_model():
    with pytest.raises(ValueError):
        EditMarker(id="m0", t_s=-1.0)


# --------------------------------------------------------------------------- #
# op_add_marker
# --------------------------------------------------------------------------- #


def test_add_marker_appends_with_deterministic_id():
    out = apply_operation(_project(), "add_marker", {"t_s": 1.5, "label": "intro"})
    assert len(out.markers) == 1
    marker = out.markers[0]
    assert marker.id == "marker_0"
    assert marker.t_s == pytest.approx(1.5)
    assert marker.label == "intro"
    assert marker.color == "#38bdf8"  # default


def test_add_marker_custom_color_and_explicit_id():
    out = apply_operation(
        _project(),
        "add_marker",
        {"t_s": 2.0, "color": "#ff0000", "marker_id": "beat"},
    )
    assert out.markers[0].id == "beat"
    assert out.markers[0].color == "#ff0000"


def test_add_marker_sequential_ids():
    out = apply_operation(_project(), "add_marker", {"t_s": 1.0})
    out = apply_operation(out, "add_marker", {"t_s": 2.0})
    assert [m.id for m in out.markers] == ["marker_0", "marker_1"]


def test_add_marker_requires_t_s():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "add_marker", {"label": "x"})


def test_add_marker_rejects_negative_t_s():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "add_marker", {"t_s": -0.5})


def test_add_marker_rejects_duplicate_explicit_id():
    out = apply_operation(_project(), "add_marker", {"t_s": 1.0, "marker_id": "beat"})
    with pytest.raises(EditOperationError):
        apply_operation(out, "add_marker", {"t_s": 2.0, "marker_id": "beat"})


def test_add_marker_is_pure():
    project = _project()
    before = project.model_dump(mode="json")
    apply_operation(project, "add_marker", {"t_s": 1.0})
    assert project.model_dump(mode="json") == before


# --------------------------------------------------------------------------- #
# op_remove_marker / op_set_marker
# --------------------------------------------------------------------------- #


def test_remove_marker():
    out = apply_operation(_project(), "add_marker", {"t_s": 1.0})
    out = apply_operation(out, "remove_marker", {"marker_id": "marker_0"})
    assert out.markers == []


def test_remove_marker_unknown():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "remove_marker", {"marker_id": "nope"})


def test_set_marker_updates_fields():
    out = apply_operation(_project(), "add_marker", {"t_s": 1.0, "label": "a"})
    out = apply_operation(
        out, "set_marker", {"marker_id": "marker_0", "t_s": 3.0, "label": "b", "color": "#00ff00"}
    )
    marker = out.markers[0]
    assert marker.t_s == pytest.approx(3.0)
    assert marker.label == "b"
    assert marker.color == "#00ff00"


def test_set_marker_partial_keeps_other_fields():
    out = apply_operation(_project(), "add_marker", {"t_s": 1.0, "label": "keep"})
    out = apply_operation(out, "set_marker", {"marker_id": "marker_0", "t_s": 5.0})
    assert out.markers[0].label == "keep"
    assert out.markers[0].t_s == pytest.approx(5.0)


def test_set_marker_unknown():
    with pytest.raises(EditOperationError):
        apply_operation(_project(), "set_marker", {"marker_id": "nope", "t_s": 1.0})


def test_set_marker_rejects_negative_t_s():
    out = apply_operation(_project(), "add_marker", {"t_s": 1.0})
    with pytest.raises(EditOperationError):
        apply_operation(out, "set_marker", {"marker_id": "marker_0", "t_s": -2.0})


# --------------------------------------------------------------------------- #
# Regresión: markers son metadatos y NO entran en inputProps
# --------------------------------------------------------------------------- #


def test_markers_not_in_inputprops():
    with_markers = apply_operation(_project(), "add_marker", {"t_s": 1.0, "label": "x"})
    props = compile_remotion_props(with_markers, lambda aid: str(aid))
    # Ninguna clave de inputProps menciona marcadores.
    assert "markers" not in props
    assert not any("marker" in str(k).lower() for k in props)


def test_regression_markers_same_inputprops():
    """Un EditProject sin markers produce EXACTAMENTE el mismo inputProps que con ellos."""
    base = _project()
    props_base = compile_remotion_props(base, lambda aid: str(aid))

    with_markers = apply_operation(base, "add_marker", {"t_s": 1.0, "label": "intro"})
    with_markers = apply_operation(with_markers, "add_marker", {"t_s": 3.5})
    props_markers = compile_remotion_props(with_markers, lambda aid: str(aid))

    assert props_markers == props_base


def test_regression_legacy_document_without_markers_validates():
    """Un documento previo (sin el campo ``markers``) valida y compila igual."""
    project = _project()
    legacy_dump = project.model_dump(mode="json")
    legacy_dump.pop("markers", None)
    legacy = EditProject.model_validate(legacy_dump)
    assert legacy.markers == []
    assert compile_remotion_props(legacy, lambda aid: str(aid)) == compile_remotion_props(
        project, lambda aid: str(aid)
    )
