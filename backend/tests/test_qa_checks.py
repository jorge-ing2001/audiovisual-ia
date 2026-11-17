"""Evaluación PURA del motor de QA (sin ffmpeg): clasificación de los 5 controles en los
4 estados + regla de ``overall`` (FEAT-004, docs/ADMIN-QUALITY.md §2.2/§2.3).

Estas pruebas NO invocan ffmpeg/ffprobe: alimentan mediciones DADAS a las funciones puras
de ``engines.qa.checks`` y comprueban el veredicto esperado por check y el ``overall``,
verificando que un ``skipped`` NUNCA produce ``pass``.
"""

from __future__ import annotations

from app.engines.qa import checks as qa


THRESHOLDS = qa.load_thresholds()


# --------------------------------------------------------------------------- #
# loudness
# --------------------------------------------------------------------------- #


def test_loudness_pass_within_1lu():
    c = qa.check_loudness(-13.5, THRESHOLDS)
    assert c["status"] == qa.STATUS_PASS
    assert c["measured"]["integrated_lufs"] == -13.5


def test_loudness_warn_between_1_and_2lu():
    c = qa.check_loudness(-12.5, THRESHOLDS)  # Δ = 1.5 LU
    assert c["status"] == qa.STATUS_WARN


def test_loudness_fail_beyond_2lu():
    c = qa.check_loudness(-10.0, THRESHOLDS)  # Δ = 4 LU
    assert c["status"] == qa.STATUS_FAIL


def test_loudness_skipped_no_audio():
    c = qa.check_loudness(None, THRESHOLDS, skip_reason="no_audio")
    assert c["status"] == qa.STATUS_SKIPPED
    assert c["skip_reason"] == "no_audio"
    assert c["measured"] is None


def test_loudness_skipped_when_value_none_without_reason():
    c = qa.check_loudness(None, THRESHOLDS)
    assert c["status"] == qa.STATUS_SKIPPED


# --------------------------------------------------------------------------- #
# black_frames
# --------------------------------------------------------------------------- #


def test_black_frames_pass_no_segments():
    c = qa.check_black_frames([], THRESHOLDS)
    assert c["status"] == qa.STATUS_PASS
    assert c["measured"]["max_black_s"] == 0.0


def test_black_frames_warn_short_segment():
    c = qa.check_black_frames([(0.0, 0.4)], THRESHOLDS)  # 0.4s < 1.0s
    assert c["status"] == qa.STATUS_WARN
    assert c["measured"]["max_black_s"] == 0.4


def test_black_frames_fail_long_segment_reports_start_and_duration():
    c = qa.check_black_frames([(2.0, 3.5)], THRESHOLDS)  # 1.5s >= 1.0s
    assert c["status"] == qa.STATUS_FAIL
    assert "t=2.00s" in c["detail"]
    assert c["measured"]["max_black_s"] == 1.5


def test_black_frames_skipped_no_ffmpeg():
    c = qa.check_black_frames(None, THRESHOLDS, skip_reason="ffmpeg_unavailable")
    assert c["status"] == qa.STATUS_SKIPPED
    assert c["skip_reason"] == "ffmpeg_unavailable"


# --------------------------------------------------------------------------- #
# duration_match
# --------------------------------------------------------------------------- #


def test_duration_match_pass_within_tolerance():
    c = qa.check_duration_match(10.3, 10.0, THRESHOLDS)  # Δ 0.3s / 3%... pct > 2%
    # 0.3s <= 0.5s pero 3% > 2% -> NO pass (requiere ambas). Debe ser warn.
    assert c["status"] == qa.STATUS_WARN


def test_duration_match_pass_strict():
    c = qa.check_duration_match(100.1, 100.0, THRESHOLDS)  # Δ 0.1s y 0.1%
    assert c["status"] == qa.STATUS_PASS


def test_duration_match_warn_band():
    c = qa.check_duration_match(100.8, 100.0, THRESHOLDS)  # Δ 0.8s (<=1s) y 0.8% (<=5%)
    assert c["status"] == qa.STATUS_WARN


def test_duration_match_fail_band():
    c = qa.check_duration_match(10.0, 5.0, THRESHOLDS)  # Δ 5s (>1s) y 100% (>5%)
    assert c["status"] == qa.STATUS_FAIL


def test_duration_match_skipped_no_expected():
    c = qa.check_duration_match(10.0, None, THRESHOLDS)
    assert c["status"] == qa.STATUS_SKIPPED
    assert c["skip_reason"] == "no_expected_duration"


def test_duration_match_skipped_no_video():
    c = qa.check_duration_match(None, 10.0, THRESHOLDS, skip_reason="no_video")
    assert c["status"] == qa.STATUS_SKIPPED
    assert c["skip_reason"] == "no_video"


# --------------------------------------------------------------------------- #
# resolution_aspect
# --------------------------------------------------------------------------- #


def test_resolution_aspect_pass_matching_aspect():
    c = qa.check_resolution_aspect(1920, 1080, THRESHOLDS, expected_aspect="16:9")
    assert c["status"] == qa.STATUS_PASS


def test_resolution_aspect_fail_wrong_aspect():
    c = qa.check_resolution_aspect(1080, 1920, THRESHOLDS, expected_aspect="16:9")
    assert c["status"] == qa.STATUS_FAIL


def test_resolution_aspect_fail_wrong_resolution():
    c = qa.check_resolution_aspect(
        1280, 720, THRESHOLDS, expected_aspect="16:9", expected_resolution=(1920, 1080)
    )
    assert c["status"] == qa.STATUS_FAIL


def test_resolution_aspect_never_warn_only_pass_fail_skipped():
    # Este control no usa 'warn' en ningún caso.
    cases = [
        qa.check_resolution_aspect(1920, 1080, THRESHOLDS, expected_aspect="16:9"),
        qa.check_resolution_aspect(1080, 1920, THRESHOLDS, expected_aspect="16:9"),
        qa.check_resolution_aspect(None, None, THRESHOLDS, skip_reason="no_video"),
    ]
    assert all(c["status"] != qa.STATUS_WARN for c in cases)


def test_resolution_aspect_skipped_no_video():
    c = qa.check_resolution_aspect(None, None, THRESHOLDS, skip_reason="no_video")
    assert c["status"] == qa.STATUS_SKIPPED


# --------------------------------------------------------------------------- #
# subtitle_sync
# --------------------------------------------------------------------------- #


def test_subtitle_sync_pass_all_in_range():
    c = qa.check_subtitle_sync([(0.0, 2.0), (2.0, 4.0)], 10.0, THRESHOLDS)
    assert c["status"] == qa.STATUS_PASS


def test_subtitle_sync_warn_small_overflow():
    c = qa.check_subtitle_sync([(0.0, 2.0), (9.0, 10.6)], 10.0, THRESHOLDS)  # excede 0.35s
    assert c["status"] == qa.STATUS_WARN


def test_subtitle_sync_fail_big_overflow():
    c = qa.check_subtitle_sync([(0.0, 12.0)], 10.0, THRESHOLDS)  # excede ~1.75s
    assert c["status"] == qa.STATUS_FAIL


def test_subtitle_sync_fail_end_before_start():
    c = qa.check_subtitle_sync([(5.0, 4.0)], 10.0, THRESHOLDS)
    assert c["status"] == qa.STATUS_FAIL


def test_subtitle_sync_warn_micro_overlap():
    c = qa.check_subtitle_sync([(0.0, 2.05), (2.0, 4.0)], 10.0, THRESHOLDS)  # solape 0.05s
    assert c["status"] == qa.STATUS_WARN


def test_subtitle_sync_fail_big_overlap():
    c = qa.check_subtitle_sync([(0.0, 3.0), (2.0, 4.0)], 10.0, THRESHOLDS)  # solape 1.0s
    assert c["status"] == qa.STATUS_FAIL


def test_subtitle_sync_skipped_no_subtitles():
    c = qa.check_subtitle_sync([], 10.0, THRESHOLDS)
    assert c["status"] == qa.STATUS_SKIPPED
    assert c["skip_reason"] == "no_subtitles"
    c2 = qa.check_subtitle_sync(None, 10.0, THRESHOLDS)
    assert c2["status"] == qa.STATUS_SKIPPED


def test_subtitle_sync_skipped_no_video_duration():
    c = qa.check_subtitle_sync([(0.0, 2.0)], None, THRESHOLDS)
    assert c["status"] == qa.STATUS_SKIPPED
    assert c["skip_reason"] == "no_video"


# --------------------------------------------------------------------------- #
# compute_overall (skipped NUNCA es pass)
# --------------------------------------------------------------------------- #


def _checks(*statuses: str) -> list[dict]:
    return [{"id": f"c{i}", "status": s} for i, s in enumerate(statuses)]


def test_overall_pass_only_when_all_pass():
    assert qa.compute_overall(_checks("pass", "pass", "pass")) == qa.OVERALL_PASS


def test_overall_pass_with_skips_when_only_skips_and_pass():
    overall = qa.compute_overall(_checks("pass", "skipped", "pass"))
    assert overall == qa.OVERALL_PASS_WITH_SKIPS
    assert overall != qa.OVERALL_PASS


def test_overall_warn_over_skipped():
    assert qa.compute_overall(_checks("pass", "warn", "skipped")) == qa.OVERALL_WARN


def test_overall_fail_dominates_everything():
    assert qa.compute_overall(_checks("fail", "warn", "skipped", "pass")) == qa.OVERALL_FAIL


def test_overall_all_skipped_is_pass_with_skips_never_pass():
    overall = qa.compute_overall(_checks("skipped", "skipped"))
    assert overall == qa.OVERALL_PASS_WITH_SKIPS
    assert overall != qa.OVERALL_PASS


def test_overall_empty_is_pass_with_skips():
    assert qa.compute_overall([]) == qa.OVERALL_PASS_WITH_SKIPS


def test_overall_precedence_fail_warn_skipped_pass():
    # Verifica el orden completo fail > warn > skipped > pass.
    assert qa.compute_overall(_checks("fail", "skipped")) == qa.OVERALL_FAIL
    assert qa.compute_overall(_checks("warn", "skipped")) == qa.OVERALL_WARN
    assert qa.compute_overall(_checks("skipped", "pass")) == qa.OVERALL_PASS_WITH_SKIPS
    assert qa.compute_overall(_checks("pass")) == qa.OVERALL_PASS
