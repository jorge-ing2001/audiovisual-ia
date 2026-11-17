"""Export del editor: re-render desde el ``EditProject`` (ffprobe o se salta con razón).

Patrón de ``tests/test_render_engine.py``: construye un ``EditProject`` mínimo con una
imagen placeholder real + voz WAV real y ejecuta ``RenderEngine.compose_edit``. Si la
composición Remotion ``Edit`` no está disponible (no existe aún —es de FEAT-003— o no hay
Chromium), ``compose_edit`` lanza ``RenderUnavailable`` y el test se SALTA con la razón
(nunca simula un MP4). El eslabón ``AudioMixer`` + ``_mux`` se ejercita por separado con
el compilador de audio del editor cuando hay ffmpeg.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import get_settings

np = pytest.importorskip("numpy", reason="numpy no instalado (tier de voz)")
sf = pytest.importorskip("soundfile", reason="soundfile no instalado (tier de voz)")


def _ffmpeg_available() -> bool:
    return shutil.which(get_settings().ffmpeg_bin) is not None


def _ffprobe_available() -> bool:
    return shutil.which(get_settings().ffprobe_bin) is not None


def _write_tone(path: Path, *, seconds: float = 2.0, freq: float = 220.0, sr: int = 24000) -> str:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    tone = (0.3 * np.sin(2 * np.pi * freq * t)).astype("float32")
    sf.write(str(path), tone, sr)
    return str(path)


def _probe_streams(ffprobe: str, path: str) -> list[str]:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _probe_duration(ffprobe: str, path: str) -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
    )
    try:
        return float(out.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


@pytest.fixture()
def render_env(temp_env):
    if not (_ffmpeg_available() and _ffprobe_available()):
        pytest.skip(
            f"ffmpeg/ffprobe no disponibles en '{get_settings().ffmpeg_bin}'. "
            "Configura AIV_FFMPEG_BIN/AIV_FFPROBE_BIN (sandbox: /projects/sandbox/.localbin)."
        )


# --------------------------------------------------------------------------- #
# 1. Eslabón AudioMixer + mux del editor (activo si hay ffmpeg, NO simula MP4)
# --------------------------------------------------------------------------- #


def test_editor_audio_compile_mix_and_mux(render_env, tmp_path):
    """compile_audio_tracks -> AudioMixer.mix -> _mux produce un MP4 con audio real."""
    from app.engines.editor.compile import compile_audio_tracks
    from app.engines.editor.model import EditClip, EditMeta, EditProject, EditTrack
    from app.engines.render import RenderEngine
    from app.engines.voice.mixer import AudioMixer

    settings = get_settings()

    # Voz real (tono) de 2 s referenciada por el clip (asset_id=1).
    tone = _write_tone(tmp_path / "voice.wav", seconds=2.0)

    edit = EditProject(
        project_id=1,
        meta=EditMeta(fps=30, width=320, height=240),
        tracks=[
            EditTrack(
                id="track_voice",
                kind="voice",
                clips=[
                    EditClip(
                        id="a0",
                        track_id="track_voice",
                        kind="voice",
                        start_s=0.0,
                        duration_s=2.0,
                        source_in_s=0.0,
                        source_out_s=2.0,
                        asset_id=1,
                        gain=1.0,
                    )
                ],
            )
        ],
    )

    tracks = compile_audio_tracks(edit, lambda aid: tone if aid == 1 else None)
    assert len(tracks) == 1
    mix = AudioMixer().mix(tracks, str(tmp_path / "edit_mix.wav"))
    assert Path(mix.path).is_file()

    # Vídeo mudo de prueba (lavfi) y mux con la mezcla del editor.
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            settings.ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=navy:s=320x240:d=2:r=30",
            "-pix_fmt", "yuv420p", str(silent),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    out = tmp_path / "edit_final.mp4"
    RenderEngine()._mux(silent, mix.path, out)

    assert out.is_file() and out.stat().st_size > 0
    streams = _probe_streams(settings.ffprobe_bin, str(out))
    assert "video" in streams
    assert "audio" in streams
    assert abs(_probe_duration(settings.ffprobe_bin, str(out)) - 2.0) <= 0.3


# --------------------------------------------------------------------------- #
# 2. compose_edit completo (se salta con razón si la composición Edit no está)
# --------------------------------------------------------------------------- #


def test_compose_edit_full_render(render_env, db_ready, tmp_path):
    from app.engines.editor.convert import timeline_to_edit_project
    from app.engines.editor.store import save_edit_project
    from app.engines.render import RenderEngine, RenderUnavailable, TimelineBuilder
    from app.engines.render.engine import FfmpegError
    from app.models.entities import Asset, Project, Scene
    from app.providers.image.placeholder import PlaceholderProvider
    from app.storage.db import session_scope
    from app.storage.media import ensure_project_workspace

    settings = get_settings()

    with session_scope() as session:
        project = Project(name="edit-export", prompt="faro en la tormenta", aspect_ratio="16:9")
        session.add(project)
        session.flush()
        scene = Scene(
            project_id=project.id,
            order=0,
            narration_text="Un faro resiste la tormenta.",
            visual_prompt="faro en la tormenta",
            duration_s=2.0,
        )
        session.add(scene)
        session.flush()

        img_dir = ensure_project_workspace(project.id) / "images"
        img = PlaceholderProvider().image_for(
            "faro en la tormenta", aspect="16:9", out_dir=str(img_dir)
        )
        voice = _write_tone(tmp_path / "voice.wav", seconds=2.0)
        session.add_all(
            [
                Asset(
                    project_id=project.id,
                    scene_id=scene.id,
                    kind="image",
                    path=img.path,
                    provider="placeholder",
                ),
                Asset(
                    project_id=project.id,
                    scene_id=scene.id,
                    kind="voice",
                    path=voice,
                    provider="kokoro",
                    meta={"duration_s": 2.0},
                ),
            ]
        )
        session.commit()
        session.refresh(project)

        # Deriva + persiste el EditProject (como hace POST /editor/open).
        timeline = TimelineBuilder().build(project, session)
        edit_project = timeline_to_edit_project(timeline, project, list(project.assets))

        # FEAT-004: ejercita el export con estilo de subtítulo + transform/transición en un
        # clip visual, verificando que las operaciones nuevas sobreviven al re-render.
        from app.engines.editor.operations import apply_operation

        for track in edit_project.tracks:
            if track.kind in ("image", "video") and track.clips:
                edit_project = apply_operation(
                    edit_project,
                    "set_transform",
                    {"clip_id": track.clips[0].id, "scale": 1.2, "x": 20.0, "fit": "contain"},
                )
                edit_project = apply_operation(
                    edit_project,
                    "set_transition",
                    {"clip_id": track.clips[0].id, "edge": "in", "type": "fade", "duration_s": 0.3},
                )
                break
        for track in edit_project.tracks:
            if track.kind == "subtitle" and track.clips:
                edit_project = apply_operation(
                    edit_project,
                    "set_style",
                    {
                        "clip_id": track.clips[0].id,
                        "preset": "tiktok",
                        "position": "center",
                        "font_size": 64,
                        "color": "#FFCC00",
                    },
                )
                break

        save_edit_project(project, session, edit_project)

        try:
            result = RenderEngine().compose_edit(project, edit_project, session)
        except RenderUnavailable as exc:
            pytest.skip(
                "Composición Remotion 'Edit' no disponible en este entorno "
                "(es de FEAT-003 / necesita Chromium headless): "
                f"{exc}"
            )
        except FfmpegError as exc:  # pragma: no cover - solo si ffmpeg falla el mux
            pytest.fail(f"El mux de FFmpeg falló: {exc}")

        video = Path(result.video_path)
        assert video.is_file() and video.stat().st_size > 0
        streams = _probe_streams(settings.ffprobe_bin, str(video))
        assert "video" in streams
        assert "audio" in streams
        duration = _probe_duration(settings.ffprobe_bin, str(video))
        assert abs(duration - 2.0) <= 0.5
        assert result.meta.get("source") == "edit_export"
        # El Asset de vídeo nuevo es descargable por GET /projects/{id}/video.
        asset = session.get(Asset, result.asset_id)
        assert asset is not None and asset.kind == "video"


# --------------------------------------------------------------------------- #
# 3. Export con subtítulo KARAOKE (FEAT-002): slice vertical end-to-end
# --------------------------------------------------------------------------- #


def test_compose_edit_karaoke_render(render_env, db_ready, tmp_path):
    """EditProject con subtítulo karaoke (palabras) + imagen placeholder + voz WAV.

    Ejercita la cadena completa modelo→op→compilador→composición Remotion→export y
    verifica con ffprobe que el MP4 tiene vídeo+audio y duración coherente. Si Remotion
    no renderiza (sin Chromium), se SALTA con razón (nunca simula el MP4)."""
    from app.engines.editor.convert import timeline_to_edit_project
    from app.engines.editor.model import SubtitleWord
    from app.engines.editor.operations import apply_operation
    from app.engines.editor.store import save_edit_project
    from app.engines.render import RenderEngine, RenderUnavailable, TimelineBuilder
    from app.engines.render.engine import FfmpegError
    from app.models.entities import Asset, Project, Scene
    from app.providers.image.placeholder import PlaceholderProvider
    from app.storage.db import session_scope
    from app.storage.media import ensure_project_workspace

    settings = get_settings()

    with session_scope() as session:
        project = Project(name="edit-karaoke", prompt="olas contra el faro", aspect_ratio="16:9")
        session.add(project)
        session.flush()
        scene = Scene(
            project_id=project.id,
            order=0,
            narration_text="Las olas golpean el faro.",
            visual_prompt="olas contra el faro",
            duration_s=2.0,
        )
        session.add(scene)
        session.flush()

        img_dir = ensure_project_workspace(project.id) / "images"
        img = PlaceholderProvider().image_for(
            "olas contra el faro", aspect="16:9", out_dir=str(img_dir)
        )
        voice = _write_tone(tmp_path / "voice.wav", seconds=2.0)
        session.add_all(
            [
                Asset(
                    project_id=project.id,
                    scene_id=scene.id,
                    kind="image",
                    path=img.path,
                    provider="placeholder",
                ),
                Asset(
                    project_id=project.id,
                    scene_id=scene.id,
                    kind="voice",
                    path=voice,
                    provider="kokoro",
                    meta={"duration_s": 2.0},
                ),
            ]
        )
        session.commit()
        session.refresh(project)

        timeline = TimelineBuilder().build(project, session)
        edit_project = timeline_to_edit_project(timeline, project, list(project.assets))

        # Adjunta palabras y activa karaoke en el primer clip de subtítulo (si existe);
        # si el timeline no produjo subtítulos, crea uno mínimo para el slice.
        sub_track = next(t for t in edit_project.tracks if t.kind == "subtitle")
        if not sub_track.clips:
            from app.engines.editor.model import EditClip

            sub_track.clips.append(
                EditClip(
                    id="clip_sub_manual",
                    track_id=sub_track.id,
                    kind="subtitle",
                    start_s=0.0,
                    duration_s=2.0,
                    text="olas faro",
                )
            )
        clip = sub_track.clips[0]
        clip.words = [
            SubtitleWord(text="olas", start_s=0.0, end_s=1.0),
            SubtitleWord(text="faro", start_s=1.0, end_s=2.0),
        ]
        edit_project = apply_operation(
            edit_project,
            "set_karaoke",
            {"clip_id": clip.id, "enabled": True, "highlight_color": "#FFE34D"},
        )

        # El compilador debe emitir words para el clip karaoke (paridad preview/export).
        from app.engines.editor.compile import compile_remotion_props

        props = compile_remotion_props(edit_project, muted=True)
        karaoke_subs = [s for s in props["subtitleClips"] if s.get("words")]
        assert karaoke_subs, "El compilador no emitió words para el subtítulo karaoke."

        save_edit_project(project, session, edit_project)

        try:
            result = RenderEngine().compose_edit(project, edit_project, session)
        except RenderUnavailable as exc:
            pytest.skip(
                "Composición Remotion 'Edit' no disponible en este entorno "
                f"(necesita Chromium headless): {exc}"
            )
        except FfmpegError as exc:  # pragma: no cover - solo si ffmpeg falla el mux
            pytest.fail(f"El mux de FFmpeg falló: {exc}")

        video = Path(result.video_path)
        assert video.is_file() and video.stat().st_size > 0
        streams = _probe_streams(settings.ffprobe_bin, str(video))
        assert "video" in streams
        assert "audio" in streams
        duration = _probe_duration(settings.ffprobe_bin, str(video))
        assert abs(duration - 2.0) <= 0.5
        assert result.meta.get("source") == "edit_export"


# --------------------------------------------------------------------------- #
# 5. Export con TIPOGRAFÍA del catálogo (FEAT-007) + POSICIÓN arrastrada (FEAT-008)
# --------------------------------------------------------------------------- #


def test_compose_edit_font_and_position_render(render_env, db_ready, tmp_path):
    """EditProject con un título que usa una fuente del catálogo y una posición movida
    por drag (op set_position), más un subtítulo con offset x/y, exportado end-to-end.

    Verifica con ffprobe que el MP4 conserva vídeo+audio y duración coherente: así el
    render headless ha cargado la tipografía del catálogo (loadFont con delayRender) y
    aplicado la posición arrastrada sin romper el pipeline. Si Remotion no renderiza
    (sin Chromium) o la fuente no puede descargarse, se SALTA con razón (nunca simula)."""
    from app.engines.editor.compile import compile_remotion_props
    from app.engines.editor.convert import timeline_to_edit_project
    from app.engines.editor.operations import apply_operation
    from app.engines.editor.store import save_edit_project
    from app.engines.render import RenderEngine, RenderUnavailable, TimelineBuilder
    from app.engines.render.engine import FfmpegError
    from app.models.entities import Asset, Project, Scene
    from app.providers.image.placeholder import PlaceholderProvider
    from app.storage.db import session_scope
    from app.storage.media import ensure_project_workspace

    settings = get_settings()

    with session_scope() as session:
        project = Project(name="edit-font-pos", prompt="faro", aspect_ratio="16:9")
        session.add(project)
        session.flush()
        scene = Scene(
            project_id=project.id,
            order=0,
            narration_text="Un faro con tipografía Montserrat.",
            visual_prompt="faro",
            duration_s=2.0,
        )
        session.add(scene)
        session.flush()

        img_dir = ensure_project_workspace(project.id) / "images"
        bg = PlaceholderProvider().image_for("faro", aspect="16:9", out_dir=str(img_dir))
        voice = _write_tone(tmp_path / "voice.wav", seconds=2.0)
        session.add_all(
            [
                Asset(
                    project_id=project.id,
                    scene_id=scene.id,
                    kind="image",
                    path=bg.path,
                    provider="placeholder",
                ),
                Asset(
                    project_id=project.id,
                    scene_id=scene.id,
                    kind="voice",
                    path=voice,
                    provider="kokoro",
                    meta={"duration_s": 2.0},
                ),
            ]
        )
        session.commit()
        session.refresh(project)

        timeline = TimelineBuilder().build(project, session)
        edit_project = timeline_to_edit_project(timeline, project, list(project.assets))

        # Título con tipografía del catálogo (Montserrat) + posición movida por drag.
        edit_project = apply_operation(edit_project, "add_track", {"kind": "title"})
        title_track = next(t for t in edit_project.tracks if t.kind == "title")
        edit_project = apply_operation(
            edit_project,
            "add_title",
            {
                "track_id": title_track.id,
                "text": "EL FARO",
                "start_s": 0.0,
                "duration_s": 2.0,
                "title": {"kind": "plain", "font_family": "Montserrat, sans-serif"},
                "animation": {"type": "fade", "duration_s": 0.3},
            },
        )
        title_track = next(t for t in edit_project.tracks if t.kind == "title")
        title_clip = title_track.clips[0]
        # Drag del título a la esquina superior izquierda (set_position => anchor).
        edit_project = apply_operation(
            edit_project,
            "set_position",
            {"clip_id": title_clip.id, "anchor_x": 0.2, "anchor_y": 0.18},
        )

        # Subtítulo con fuente del catálogo + offset x/y arrastrado.
        sub_track = next(t for t in edit_project.tracks if t.kind == "subtitle")
        if not sub_track.clips:
            from app.engines.editor.model import EditClip

            sub_track.clips.append(
                EditClip(
                    id="clip_sub_fp",
                    track_id=sub_track.id,
                    kind="subtitle",
                    start_s=0.0,
                    duration_s=2.0,
                    text="faro con fuente",
                )
            )
        sub_clip = sub_track.clips[0]
        edit_project = apply_operation(
            edit_project,
            "set_style",
            {"clip_id": sub_clip.id, "font_family": "Oswald, sans-serif"},
        )
        edit_project = apply_operation(
            edit_project,
            "set_position",
            {"clip_id": sub_clip.id, "x": 30.0, "y": -40.0},
        )

        # El compilador propaga la familia del catálogo y la posición a inputProps.
        props = compile_remotion_props(edit_project, muted=True)
        title_props = props["titleClips"][0]
        assert title_props["style"]["fontFamily"] == "Montserrat, sans-serif"
        assert title_props["style"]["anchorX"] == pytest.approx(0.2)
        assert title_props["style"]["anchorY"] == pytest.approx(0.18)
        sub_props = next(s for s in props["subtitleClips"] if s["id"] == sub_clip.id)
        assert sub_props["style"]["fontFamily"] == "Oswald, sans-serif"
        assert sub_props["style"]["x"] == pytest.approx(30.0)
        assert sub_props["style"]["y"] == pytest.approx(-40.0)

        save_edit_project(project, session, edit_project)

        try:
            result = RenderEngine().compose_edit(project, edit_project, session)
        except RenderUnavailable as exc:
            pytest.skip(
                "Composición Remotion 'Edit' no disponible en este entorno "
                f"(necesita Chromium headless, o la fuente del catálogo no se pudo "
                f"descargar): {exc}"
            )
        except FfmpegError as exc:  # pragma: no cover - solo si ffmpeg falla el mux
            pytest.fail(f"El mux de FFmpeg falló: {exc}")

        video = Path(result.video_path)
        assert video.is_file() and video.stat().st_size > 0
        streams = _probe_streams(settings.ffprobe_bin, str(video))
        assert "video" in streams
        assert "audio" in streams
        duration = _probe_duration(settings.ffprobe_bin, str(video))
        assert abs(duration - 2.0) <= 0.5
        assert result.meta.get("source") == "edit_export"

# --------------------------------------------------------------------------- #
# 4. Export con TÍTULO animado + OVERLAY/logo (FEAT-005/006)
# --------------------------------------------------------------------------- #


def test_compose_edit_title_and_overlay_render(render_env, db_ready, tmp_path):
    """EditProject con un título animado + un overlay/logo (imagen real) + voz WAV.

    Ejercita modelo→op(add_track/add_title/add_overlay)→compilador→composición Remotion→
    export y verifica con ffprobe que el MP4 conserva vídeo+audio y duración coherente
    (el logo se conserva en el MP4 exportado). Si Remotion no renderiza, se SALTA."""
    from app.engines.editor.compile import compile_remotion_props
    from app.engines.editor.convert import timeline_to_edit_project
    from app.engines.editor.operations import apply_operation
    from app.engines.editor.store import save_edit_project
    from app.engines.render import RenderEngine, RenderUnavailable, TimelineBuilder
    from app.engines.render.engine import FfmpegError
    from app.models.entities import Asset, Project, Scene
    from app.providers.image.placeholder import PlaceholderProvider
    from app.storage.db import session_scope
    from app.storage.media import ensure_project_workspace

    settings = get_settings()

    with session_scope() as session:
        project = Project(name="edit-title-overlay", prompt="faro", aspect_ratio="16:9")
        session.add(project)
        session.flush()
        scene = Scene(
            project_id=project.id,
            order=0,
            narration_text="Un faro al amanecer.",
            visual_prompt="faro",
            duration_s=2.0,
        )
        session.add(scene)
        session.flush()

        img_dir = ensure_project_workspace(project.id) / "images"
        bg = PlaceholderProvider().image_for("faro", aspect="16:9", out_dir=str(img_dir))
        # Logo/overlay: otra imagen placeholder real, a nivel de proyecto (scene_id=None).
        logo_dir = ensure_project_workspace(project.id) / "overlays" / "uploads"
        logo_dir.mkdir(parents=True, exist_ok=True)
        logo = PlaceholderProvider().image_for("logo", aspect="1:1", out_dir=str(logo_dir))
        voice = _write_tone(tmp_path / "voice.wav", seconds=2.0)

        logo_asset = Asset(
            project_id=project.id,
            scene_id=None,
            kind="image",
            path=logo.path,
            provider="upload",
            meta={"source": "upload", "role": "overlay"},
        )
        session.add_all(
            [
                Asset(
                    project_id=project.id,
                    scene_id=scene.id,
                    kind="image",
                    path=bg.path,
                    provider="placeholder",
                ),
                Asset(
                    project_id=project.id,
                    scene_id=scene.id,
                    kind="voice",
                    path=voice,
                    provider="kokoro",
                    meta={"duration_s": 2.0},
                ),
                logo_asset,
            ]
        )
        session.commit()
        session.refresh(project)
        session.refresh(logo_asset)

        timeline = TimelineBuilder().build(project, session)
        edit_project = timeline_to_edit_project(timeline, project, list(project.assets))

        # Añade pista + título animado y pista + overlay con el logo subido.
        edit_project = apply_operation(edit_project, "add_track", {"kind": "title"})
        title_track = next(t for t in edit_project.tracks if t.kind == "title")
        edit_project = apply_operation(
            edit_project,
            "add_title",
            {
                "track_id": title_track.id,
                "text": "EL FARO",
                "start_s": 0.0,
                "duration_s": 2.0,
                "title": {"kind": "intro"},
                "animation": {"type": "scale_in", "duration_s": 0.5},
            },
        )
        edit_project = apply_operation(edit_project, "add_track", {"kind": "overlay"})
        overlay_track = next(t for t in edit_project.tracks if t.kind == "overlay")
        edit_project = apply_operation(
            edit_project,
            "add_overlay",
            {
                "track_id": overlay_track.id,
                "asset_id": logo_asset.id,
                "start_s": 0.0,
                "duration_s": 2.0,
                "overlay": {
                    "opacity": 0.8,
                    "width_frac": 0.2,
                    "anchor_x": 0.85,
                    "anchor_y": 0.15,
                },
            },
        )

        # El compilador emite titleClips y overlayClips (paridad preview/export).
        props = compile_remotion_props(edit_project, muted=True)
        assert props.get("titleClips"), "El compilador no emitió titleClips."
        assert props.get("overlayClips"), "El compilador no emitió overlayClips."

        save_edit_project(project, session, edit_project)

        try:
            result = RenderEngine().compose_edit(project, edit_project, session)
        except RenderUnavailable as exc:
            pytest.skip(
                "Composición Remotion 'Edit' no disponible en este entorno "
                f"(necesita Chromium headless): {exc}"
            )
        except FfmpegError as exc:  # pragma: no cover - solo si ffmpeg falla el mux
            pytest.fail(f"El mux de FFmpeg falló: {exc}")

        video = Path(result.video_path)
        assert video.is_file() and video.stat().st_size > 0
        streams = _probe_streams(settings.ffprobe_bin, str(video))
        assert "video" in streams
        assert "audio" in streams
        duration = _probe_duration(settings.ffprobe_bin, str(video))
        assert abs(duration - 2.0) <= 0.5
        assert result.meta.get("source") == "edit_export"
