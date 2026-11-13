"""FIX pipeline: imagen por escena, audio secuenciado y subtítulos (fix/pipeline).

Verifica, con fixtures etiquetados y comprobaciones objetivas (ffprobe sobre audio/MP4
reales), los tres arreglos del pipeline de composición:

- BUG 1 — IMAGEN POR ESCENA: cada escena produce su propio ``Asset(kind='image')`` y el
  timeline/inputProps referencia una imagen DISTINTA por escena (no una sola global).
- BUG 2 — AUDIO SECUENCIADO: las voces de las escenas NO suenan en paralelo desde t=0;
  cada voz arranca en el offset temporal de su escena (``adelay``) y la mezcla dura
  ~ la suma de las escenas. Se comprueba midiendo la energía por tramos con ffprobe
  (``astats`` por segmento) sobre una mezcla real.
- BUG 3 — SUBTÍTULOS: el pipeline produce un ``Asset(kind='srt')`` (ASR real si está, o
  derivado del texto de narración por escena como fallback determinista etiquetado) y el
  timeline reparte cues por escena; el .srt tiene intervalos crecientes y coherentes.

Cuando un eslabón no es ejecutable aquí (sin ffmpeg, sin Chromium para Remotion) el test
concreto se SALTA con razón explícita; nunca se simula un MP4 ni una transcripción.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import get_settings

np = pytest.importorskip("numpy", reason="numpy no instalado (tier de voz)")
sf = pytest.importorskip("soundfile", reason="soundfile no instalado (tier de voz)")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _ffmpeg_tools_available() -> bool:
    s = get_settings()
    return shutil.which(s.ffmpeg_bin) is not None and shutil.which(s.ffprobe_bin) is not None


def _write_tone(path: Path, *, seconds: float, freq: float = 220.0, amp: float = 0.3,
                sr: int = 24000) -> str:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    tone = (amp * np.sin(2 * np.pi * freq * t)).astype("float32")
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), tone, sr)
    return str(path)


def _probe_duration(path: str) -> float:
    out = subprocess.run(
        [get_settings().ffprobe_bin, "-v", "error", "-show_entries",
         "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


def _probe_streams(path: str) -> list[str]:
    out = subprocess.run(
        [get_settings().ffprobe_bin, "-v", "error", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _segment_rms_db(path: str, start_s: float, end_s: float) -> float:
    """RMS (dBFS) de la porción ``[start_s, end_s)`` de un audio, vía ffmpeg astats.

    Sirve para comprobar objetivamente DÓNDE hay energía de voz: un tramo con voz tiene
    un RMS mucho mayor (menos negativo) que un tramo de silencio.
    """
    s = get_settings()
    proc = subprocess.run(
        [
            s.ffmpeg_bin, "-hide_banner", "-nostats",
            "-ss", f"{start_s}", "-to", f"{end_s}", "-i", path,
            "-af", "astats=metadata=1:reset=0", "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    stderr = proc.stderr or ""
    # Tomamos el "Overall" RMS level (última aparición de "RMS level dB").
    best: float | None = None
    for line in stderr.splitlines():
        line = line.strip()
        if "RMS level dB" in line:
            token = line.split(":", 1)[-1].strip()
            try:
                best = float(token)
            except ValueError:
                continue
    # Silencio total -> ffmpeg puede reportar "-inf": lo tratamos como muy bajo.
    if best is None:
        return -120.0
    return best


# --------------------------------------------------------------------------- #
# PORTABILIDAD — intérprete de lemo-opuscar multiplataforma (_lemo.py)
# --------------------------------------------------------------------------- #


def test_lemo_python_is_cross_platform(temp_env, tmp_path, monkeypatch):
    """``lemo_python`` resuelve el venv de lemo en POSIX y Windows, con override/fallback."""
    import sys

    from app.providers.tts import _lemo

    # Simula un lemo-opuscar con venv POSIX (.venv/bin/python).
    lemo = tmp_path / "lemo"
    posix_py = lemo / ".venv" / "bin" / "python"
    posix_py.parent.mkdir(parents=True, exist_ok=True)
    posix_py.write_text("#!/bin/sh\n")
    monkeypatch.setenv("AIV_LEMO_OPUSCAR_PATH", str(lemo))
    import app.config as config
    config._settings = None
    assert _lemo.lemo_python() == str(posix_py)

    # Simula un lemo con venv Windows (.venv/Scripts/python.exe).
    lemo_win = tmp_path / "lemo_win"
    win_py = lemo_win / ".venv" / "Scripts" / "python.exe"
    win_py.parent.mkdir(parents=True, exist_ok=True)
    win_py.write_text("")
    monkeypatch.setenv("AIV_LEMO_OPUSCAR_PATH", str(lemo_win))
    config._settings = None
    assert _lemo.lemo_python() == str(win_py)

    # Sin venv propio de lemo: fallback a sys.executable (no rompe el pipeline).
    lemo_empty = tmp_path / "lemo_empty"
    lemo_empty.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AIV_LEMO_OPUSCAR_PATH", str(lemo_empty))
    config._settings = None
    assert _lemo.lemo_python() == sys.executable

    # Override explícito por env var.
    override = tmp_path / "mypy"
    override.write_text("")
    monkeypatch.setenv("AIV_LEMO_PYTHON", str(override))
    assert _lemo.lemo_python() == str(override)


# --------------------------------------------------------------------------- #
# BUG 2 — AUDIO SECUENCIADO (unidad del mixer + mezcla real con ffprobe)
# --------------------------------------------------------------------------- #


def test_mix_filter_positions_tracks_with_adelay():
    """El grafo del mixer aplica ``adelay`` a las pistas con ``delay_s`` > 0 (unidad)."""
    from app.engines.voice.mixer import AudioMixer, MixTrack

    mixer = AudioMixer.__new__(AudioMixer)  # sin tocar ffmpeg (solo construimos el filtro)
    tracks = [
        MixTrack(path="a.wav", gain=1.0, delay_s=0.0),
        MixTrack(path="b.wav", gain=1.0, delay_s=2.5),
    ]
    graph = mixer._mix_filter(tracks)
    # La segunda pista (escena 2) se retrasa 2500 ms; la primera arranca en t=0.
    assert "adelay=2500:all=1" in graph
    assert graph.count("adelay") == 1  # solo la que tiene delay > 0
    assert "amix=inputs=2" in graph


def test_single_track_with_delay_is_positioned():
    """Una única pista con delay también se posiciona (no se pasa como anull directo)."""
    from app.engines.voice.mixer import AudioMixer, MixTrack

    mixer = AudioMixer.__new__(AudioMixer)
    graph = mixer._mix_filter([MixTrack(path="a.wav", delay_s=1.0)])
    assert "adelay=1000:all=1" in graph


@pytest.fixture()
def ff(temp_env):
    if not _ffmpeg_tools_available():
        pytest.skip(
            f"ffmpeg/ffprobe no disponibles en '{get_settings().ffmpeg_bin}'. "
            "Configura AIV_FFMPEG_BIN/AIV_FFPROBE_BIN (sandbox: /projects/sandbox/.localbin)."
        )


def test_scene_voices_are_sequenced_not_overlapped(ff, tmp_path):
    """Dos voces de escena de duraciones distintas quedan SECUENCIADAS en la mezcla.

    Escena 1: voz de 1.0 s en [0, 1). Escena 2: voz de 2.0 s en [1, 3). Tras el fix, la
    voz de la escena 2 arranca en t=1 s (offset de su escena), no en t=0. Comprobamos con
    ffprobe que:
      - el tramo [0, 1) tiene energía (voz escena 1),
      - el tramo [1.2, 2.8) tiene energía (voz escena 2),
      - la duración total ~ 3 s (suma de escenas), no 2 s (longest en paralelo).
    """
    from app.engines.voice.mixer import AudioMixer, MixTrack

    v1 = _write_tone(tmp_path / "v1.wav", seconds=1.0, freq=220.0)
    v2 = _write_tone(tmp_path / "v2.wav", seconds=2.0, freq=330.0)

    out = tmp_path / "mix.wav"
    AudioMixer().mix(
        [
            MixTrack(path=v1, gain=1.0, delay_s=0.0),   # escena 1 en t=0
            MixTrack(path=v2, gain=1.0, delay_s=1.0),   # escena 2 en t=1 s
        ],
        str(out),
    )

    # Duración ~ suma de escenas (3 s), NO 2 s (que sería el caso superpuesto/longest).
    duration = _probe_duration(str(out))
    assert duration >= 2.8, f"la mezcla secuenciada debe durar ~3 s, duró {duration:.2f}s"

    rms_scene1 = _segment_rms_db(str(out), 0.1, 0.9)   # dentro de la voz escena 1
    rms_gap_none = _segment_rms_db(str(out), 1.2, 2.8)  # dentro de la voz escena 2
    # Ambos tramos tienen energía de voz (RMS alto): cada escena suena en SU ventana.
    assert rms_scene1 > -40.0, f"escena 1 sin energía en su ventana (RMS={rms_scene1})"
    assert rms_gap_none > -40.0, f"escena 2 sin energía en su ventana (RMS={rms_gap_none})"


def test_audio_tracks_use_timeline_offsets(ff, tmp_path):
    """``RenderEngine._audio_tracks`` asigna a cada voz el offset de su escena (del timeline)."""
    from app.engines.render import RenderEngine
    from app.engines.render.timeline import TimelineBuilder
    from app.models.entities import Asset, Project, Scene, Script
    from app.storage.db import init_db, session_scope

    init_db()
    with session_scope() as session:
        project = Project(name="seq", prompt="p", aspect_ratio="16:9")
        session.add(project)
        session.flush()
        session.add(Script(project_id=project.id, language="es", raw_text="..."))
        s1 = Scene(project_id=project.id, order=0, narration_text="Uno.", visual_prompt="a",
                   duration_s=1.0)
        s2 = Scene(project_id=project.id, order=1, narration_text="Dos.", visual_prompt="b",
                   duration_s=2.0)
        session.add_all([s1, s2])
        session.flush()

        w1 = _write_tone(tmp_path / "w1.wav", seconds=1.0)
        w2 = _write_tone(tmp_path / "w2.wav", seconds=2.0)
        session.add_all([
            Asset(project_id=project.id, scene_id=s1.id, kind="voice", path=w1,
                  provider="test", meta={"duration_s": 1.0}),
            Asset(project_id=project.id, scene_id=s2.id, kind="voice", path=w2,
                  provider="test", meta={"duration_s": 2.0}),
        ])
        session.commit()
        session.refresh(project)

        timeline = TimelineBuilder().build(project, session)
        tracks = RenderEngine._audio_tracks(project, timeline)

    # Dos pistas de voz: la de la escena 1 en delay 0 s, la de la escena 2 en delay 1 s.
    assert len(tracks) == 2
    delays = sorted(t.delay_s for t in tracks)
    assert delays[0] == pytest.approx(0.0)
    assert delays[1] == pytest.approx(1.0)  # = duración de la escena 1


# --------------------------------------------------------------------------- #
# BUG 1 — IMAGEN POR ESCENA
# --------------------------------------------------------------------------- #


def test_each_scene_gets_its_own_image_asset(temp_env):
    """El motor visual crea un ``Asset(kind='image')`` DISTINTO por cada escena."""
    from app.engines.visual2d import VisualEngine
    from app.models.entities import Project, Scene, Script
    from app.storage.db import init_db, session_scope

    init_db()
    with session_scope() as session:
        project = Project(name="imgs", prompt="p", aspect_ratio="16:9",
                          provider_overrides={"image": "placeholder"})
        session.add(project)
        session.flush()
        session.add(Script(project_id=project.id, language="es", raw_text="..."))
        scenes = [
            Scene(project_id=project.id, order=i, narration_text=f"Escena {i}.",
                  visual_prompt=prompt, duration_s=1.0)
            for i, prompt in enumerate(
                ["un bosque al amanecer", "una ciudad de noche", "un desierto rojo"]
            )
        ]
        session.add_all(scenes)
        session.flush()

        engine = VisualEngine()
        for scene in scenes:
            engine.render_scene_image(scene, session)
        session.refresh(project)

        images = [a for a in project.assets if a.kind == "image"]
        # Una imagen por escena, cada una ligada a su scene_id.
        assert len(images) == 3
        assert {a.scene_id for a in images} == {s.id for s in scenes}
        # Las rutas de imagen son DISTINTAS (no una sola imagen global reutilizada).
        paths = [a.path for a in images]
        assert len(set(paths)) == 3
        for p in paths:
            assert Path(p).is_file()


def test_timeline_input_props_reference_distinct_images_per_scene(temp_env):
    """El timeline/inputProps de Remotion tiene un ``imageSrc`` DISTINTO por escena."""
    from app.engines.render.timeline import TimelineBuilder
    from app.engines.visual2d import VisualEngine
    from app.models.entities import Project, Scene, Script
    from app.storage.db import init_db, session_scope
    from app.storage.media import media_root

    init_db()
    with session_scope() as session:
        project = Project(name="imgs2", prompt="p", aspect_ratio="16:9",
                          provider_overrides={"image": "placeholder"})
        session.add(project)
        session.flush()
        session.add(Script(project_id=project.id, language="es", raw_text="..."))
        scenes = [
            Scene(project_id=project.id, order=i, narration_text=f"Escena {i}.",
                  visual_prompt=prompt, duration_s=1.0)
            for i, prompt in enumerate(["montaña nevada", "playa tropical"])
        ]
        session.add_all(scenes)
        session.flush()
        engine = VisualEngine()
        for scene in scenes:
            engine.render_scene_image(scene, session)
        session.refresh(project)

        timeline = TimelineBuilder().build(project, session)
        props = timeline.remotion_input_props(public_dir=str(media_root()))

    image_srcs = [sc["imageSrc"] for sc in props["scenes"]]
    assert len(image_srcs) == 2
    assert all(src for src in image_srcs)  # ninguna escena sin imagen
    assert len(set(image_srcs)) == 2  # imágenes DISTINTAS por escena (no una global)


# --------------------------------------------------------------------------- #
# BUG 3 — SUBTÍTULOS (fallback determinista derivado del texto + reparto por escena)
# --------------------------------------------------------------------------- #


def test_cues_from_text_distributes_over_scene_duration():
    """``cues_from_text`` reparte el texto en la ventana de la escena (unidad)."""
    from app.engines.voice.subtitles import cues_from_text

    cues = cues_from_text(
        "una gota de agua inicia su largo viaje hacia el mar",
        start_s=2.0, duration_s=4.0, max_words_per_cue=5,
    )
    assert cues, "debe producir al menos un cue"
    # Todos los cues caen dentro de la ventana [2, 6] y son crecientes.
    assert cues[0].t0 == pytest.approx(2.0)
    assert cues[-1].t1 == pytest.approx(6.0)
    for a, b in zip(cues, cues[1:]):
        assert a.t1 <= b.t0 + 1e-6
        assert a.t0 < a.t1


def test_subtitles_text_fallback_when_asr_unavailable(temp_env, monkeypatch):
    """Sin ASR, el motor deriva subtítulos del texto por escena (etiquetado text-derived)."""
    from app.engines.voice import VoiceEngine
    from app.models.entities import Asset, Project, Scene, Script
    from app.providers.asr.faster_whisper import AsrNotAvailable
    from app.storage.db import init_db, session_scope
    from app.storage.media import ensure_project_workspace

    init_db()

    # Fuerza el ASR como NO disponible (sin faster-whisper/modelo), como en el sandbox.
    class _NoAsr:
        name = "noasr"

        def transcribe(self, path):
            raise AsrNotAvailable("faster-whisper no instalado (test)")

    from app.providers import registry
    registry.register("asr", "noasr", _NoAsr)
    monkeypatch.setenv("AIV_ASR_BACKEND", "noasr")
    import app.config as config
    config._settings = None

    try:
        with session_scope() as session:
            project = Project(name="subs", prompt="p")
            session.add(project)
            session.flush()
            session.add(Script(project_id=project.id, language="es", raw_text="..."))
            s1 = Scene(project_id=project.id, order=0,
                       narration_text="Una gota de agua inicia su viaje.",
                       visual_prompt="gota", duration_s=2.0)
            s2 = Scene(project_id=project.id, order=1,
                       narration_text="El río la lleva hasta el mar.",
                       visual_prompt="rio", duration_s=2.0)
            session.add_all([s1, s2])
            session.flush()
            # Voz de test por escena con duraciones reales en meta (como el TTS).
            vdir = ensure_project_workspace(project.id) / "voice"
            vdir.mkdir(parents=True, exist_ok=True)
            w1 = vdir / "s1.wav"
            w1.write_bytes(b"RIFF" + b"\x00" * 40)
            w2 = vdir / "s2.wav"
            w2.write_bytes(b"RIFF" + b"\x00" * 40)
            session.add_all([
                Asset(project_id=project.id, scene_id=s1.id, kind="voice", path=str(w1),
                      provider="test", meta={"duration_s": 2.0}),
                Asset(project_id=project.id, scene_id=s2.id, kind="voice", path=str(w2),
                      provider="test", meta={"duration_s": 2.0}),
            ])
            session.commit()
            session.refresh(project)

            asset = VoiceEngine().generate_subtitles(project, session)
            assert asset is not None
            assert asset.kind == "srt"
            # Etiquetado como derivado del texto (NO transcripción real de ASR).
            assert asset.provider == "text-derived"
            assert (asset.meta or {}).get("source") == "text"
            srt_path = asset.path
    finally:
        registry.PROVIDERS["asr"].pop("noasr", None)
        config._settings = None

    content = Path(srt_path).read_text(encoding="utf-8")
    # El .srt tiene texto de AMBAS escenas y arranca en 00:00:00.
    assert "00:00:00,000 -->" in content
    assert "gota" in content.lower()
    assert "mar" in content.lower()
    # La segunda escena arranca a partir de los 2 s (offset acumulado): aparece 00:00:02.
    assert "00:00:02" in content


def test_pipeline_srt_cues_reach_timeline_per_scene(temp_env, monkeypatch):
    """De punta a punta (sin render): el .srt del proyecto llega al timeline como cues/escena."""
    from app.engines.render.timeline import TimelineBuilder
    from app.engines.visual2d import VisualEngine
    from app.engines.voice import VoiceEngine
    from app.models.entities import Asset, Project, Scene, Script
    from app.providers.asr.faster_whisper import AsrNotAvailable
    from app.storage.db import init_db, session_scope
    from app.storage.media import ensure_project_workspace

    init_db()

    class _NoAsr:
        name = "noasr2"

        def transcribe(self, path):
            raise AsrNotAvailable("sin ASR (test)")

    from app.providers import registry
    registry.register("asr", "noasr2", _NoAsr)
    monkeypatch.setenv("AIV_ASR_BACKEND", "noasr2")
    import app.config as config
    config._settings = None

    try:
        with session_scope() as session:
            project = Project(name="e2e-subs", prompt="p", aspect_ratio="16:9",
                              provider_overrides={"image": "placeholder"})
            session.add(project)
            session.flush()
            session.add(Script(project_id=project.id, language="es", raw_text="..."))
            s1 = Scene(project_id=project.id, order=0,
                       narration_text="Primera escena con su propio texto.",
                       visual_prompt="uno", duration_s=2.0)
            s2 = Scene(project_id=project.id, order=1,
                       narration_text="Segunda escena con otra narración.",
                       visual_prompt="dos", duration_s=2.0)
            session.add_all([s1, s2])
            session.flush()

            vdir = ensure_project_workspace(project.id) / "voice"
            vdir.mkdir(parents=True, exist_ok=True)
            w1 = vdir / "s1.wav"
            w1.write_bytes(b"RIFF" + b"\x00" * 40)
            w2 = vdir / "s2.wav"
            w2.write_bytes(b"RIFF" + b"\x00" * 40)
            session.add_all([
                Asset(project_id=project.id, scene_id=s1.id, kind="voice", path=str(w1),
                      provider="test", meta={"duration_s": 2.0}),
                Asset(project_id=project.id, scene_id=s2.id, kind="voice", path=str(w2),
                      provider="test", meta={"duration_s": 2.0}),
            ])
            session.commit()
            session.refresh(project)

            engine = VisualEngine()
            for scene in (s1, s2):
                engine.render_scene_image(scene, session)
            session.refresh(project)

            VoiceEngine().generate_subtitles(project, session)
            session.refresh(project)

            timeline = TimelineBuilder().build(project, session)

        # Ambas escenas del timeline tienen cues (los subtítulos llegan al render).
        assert len(timeline.scenes) == 2
        for tscene in timeline.scenes:
            assert tscene.cues, f"la escena {tscene.id} no recibió cues"
        # Los cues de cada escena están en coordenadas locales (arrancan cerca de 0).
        assert timeline.scenes[0].cues[0].start_s == pytest.approx(0.0, abs=0.05)
        assert timeline.scenes[1].cues[0].start_s == pytest.approx(0.0, abs=0.3)
    finally:
        registry.PROVIDERS["asr"].pop("noasr2", None)
        config._settings = None


# --------------------------------------------------------------------------- #
# END-TO-END: MP4 real con imagen por escena + voz secuenciada + subtítulos quemados
# --------------------------------------------------------------------------- #


def test_full_pipeline_mp4_has_sequenced_audio(ff, tmp_path):
    """``RenderEngine.compose`` produce un MP4 con vídeo+audio; audio ~ suma de escenas.

    Si Remotion no puede renderizar aquí (sin Chromium/glibc), se SALTA con razón.
    """
    from app.engines.render import RenderEngine, RenderUnavailable
    from app.engines.render.engine import FfmpegError
    from app.engines.visual2d import VisualEngine
    from app.engines.voice import VoiceEngine
    from app.models.entities import Asset, Project, Scene, Script
    from app.providers.asr.faster_whisper import AsrNotAvailable
    from app.storage.db import init_db, session_scope
    from app.storage.media import ensure_project_workspace

    init_db()

    class _NoAsr:
        name = "noasr3"

        def transcribe(self, path):
            raise AsrNotAvailable("sin ASR (test)")

    from app.providers import registry
    registry.register("asr", "noasr3", _NoAsr)
    import os

    import app.config as config
    os.environ["AIV_ASR_BACKEND"] = "noasr3"
    config._settings = None

    try:
        with session_scope() as session:
            project = Project(name="mp4", prompt="p", aspect_ratio="16:9",
                              provider_overrides={"image": "placeholder"})
            session.add(project)
            session.flush()
            session.add(Script(project_id=project.id, language="es", raw_text="..."))
            s1 = Scene(project_id=project.id, order=0, narration_text="Escena uno habla.",
                       visual_prompt="bosque verde", duration_s=1.0)
            s2 = Scene(project_id=project.id, order=1, narration_text="Escena dos responde.",
                       visual_prompt="mar azul", duration_s=2.0)
            session.add_all([s1, s2])
            session.flush()

            engine = VisualEngine()
            for scene in (s1, s2):
                engine.render_scene_image(scene, session)

            vdir = ensure_project_workspace(project.id) / "voice"
            w1 = _write_tone(Path(vdir) / "s1.wav", seconds=1.0, freq=220.0)
            w2 = _write_tone(Path(vdir) / "s2.wav", seconds=2.0, freq=330.0)
            session.add_all([
                Asset(project_id=project.id, scene_id=s1.id, kind="voice", path=w1,
                      provider="test", meta={"duration_s": 1.0}),
                Asset(project_id=project.id, scene_id=s2.id, kind="voice", path=w2,
                      provider="test", meta={"duration_s": 2.0}),
            ])
            session.commit()
            session.refresh(project)

            VoiceEngine().generate_subtitles(project, session)
            session.refresh(project)

            try:
                result = RenderEngine().compose(project, session)
            except RenderUnavailable as exc:
                pytest.skip(f"Render de Remotion no disponible aquí: {exc}")
            except FfmpegError as exc:  # pragma: no cover
                pytest.fail(f"mux FFmpeg falló: {exc}")

            video = Path(result.video_path)
            assert video.is_file() and video.stat().st_size > 0
            streams = _probe_streams(str(video))
            assert "video" in streams
            assert "audio" in streams
            # Duración total ~ suma de escenas (1 + 2 = 3 s), no 2 s (voces en paralelo).
            duration = _probe_duration(str(video))
            assert duration >= 2.7, (
                f"MP4 debería durar ~3 s (escenas secuenciadas), {duration:.2f}s"
            )
    finally:
        registry.PROVIDERS["asr"].pop("noasr3", None)
        os.environ.pop("AIV_ASR_BACKEND", None)
        config._settings = None
