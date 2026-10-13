"""Camera-v2 protocol, lossless evidence and shared replay regressions.

Generated frames prove software contracts, never physical robot accuracy.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from packages.cv.signoff_cv.pipeline import MeasurementPipeline, analyze_frames, analyze_video
from packages.cv.signoff_cv.source import Frame, VideoFileSource, _load_clock, pixel_hash
from packages.cv.signoff_cv.task import MarkerTaskDetector, MotionCycleDetector
from packages.domain.profiles import validate_profile
from services.live_worker.engine import EventBus, LiveEngine, SyntheticLiveSource, make_source
from services.video_worker.recorder import RecordingUnavailable, SegmentedRecorder


def profile(method="aruco"):
    return validate_profile(
        {
            "profile_id": "station",
            "camera": {"width": 320, "height": 240},
            "detector": {
                "method": method,
                "home": [0.05, 0.05, 0.25, 0.3],
                "pick": [0.05, 0.6, 0.25, 0.3],
                "place": [0.65, 0.6, 0.25, 0.3],
            },
        }
    )


def image(region="HOME", ref_shift=0, missing=False):
    img = np.full((240, 320, 3), 255, np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    ref = cv2.aruco.generateImageMarker(dictionary, 0, 40)
    img[20:60, 220 + ref_shift : 260 + ref_shift] = ref[:, :, None]
    if not missing:
        positions = {"HOME": (35, 30), "PICK": (35, 165), "PLACE": (230, 165), "OUTSIDE": (140, 95)}
        x, y = positions[region]
        marker = cv2.aruco.generateImageMarker(dictionary, 1, 40)
        img[y : y + 40, x : x + 40] = marker[:, :, None]
    return img


def task_frames(regions=None):
    regions = (
        regions or ["HOME"] * 4 + ["OUTSIDE"] * 2 + ["PICK"] * 3 + ["PLACE"] * 3 + ["HOME"] * 4
    )
    return [
        Frame(n, n * 100.0, image(region), "session", clock_basis="synthetic")
        for n, region in enumerate(regions)
    ]


def recorder(tmp_path, **kw):
    return SegmentedRecorder(
        tmp_path,
        "session",
        min_start_bytes=0,
        min_remaining_bytes=100,
        free_space=lambda: 100000,
        **kw,
    )


def test_marker_cycle_finalizes_after_home_dwell_and_preserves_first_boundary():
    pipeline = MeasurementPipeline(profile())
    frames = task_frames()
    for frame in frames[:14]:
        pipeline.consume(frame)
    assert pipeline.completed_cycles == 0  # HOME has only 100ms, not 200ms yet
    pipeline.consume(frames[14])
    assert pipeline.completed_cycles == 1
    cycle = pipeline.result()["detected_cycles"][0]
    assert (cycle["start_ms"], cycle["end_ms"]) == (400, 1200)
    assert cycle["outcome"] == "UNKNOWN"
    pipeline.consume(frames[15])
    pipeline.finish()
    assert len(pipeline.result()["detected_cycles"]) == 1


def test_incomplete_eof_is_partial_not_complete():
    result = analyze_frames(task_frames()[:12], profile())
    assert result["detected_cycles"] == []
    assert result["partial_cycles"][0]["reason"] == "END_OF_RECORDING"


def test_skipped_pick_rejects_visible_completion():
    result = analyze_frames(task_frames(["HOME"] * 4 + ["PLACE"] * 3 + ["HOME"] * 4), profile())
    assert result["detected_cycles"] == []
    assert result["partial_cycles"][0]["reason"] == "REQUIRED_REGION_SKIPPED"


def test_visibility_gap_requires_stable_home_reacquisition():
    frames = task_frames()
    for n in (6, 7, 8, 9):
        frames[n].image = image(missing=True)
    result = analyze_frames(frames, profile())
    assert not result["detected_cycles"]
    assert "MARKER_NOT_VISIBLE" in result["quality_flags"]
    assert result["partial_cycles"][0]["reason"] == "MARKER_VISIBILITY_GAP"
    assert result["coverage"]["unknown_ms"] > 0
    durations = result["coverage"]["unknown_ms"] + result["coverage"]["observed_ms"]
    assert durations == result["video_duration_ms"]


def test_camera_movement_persistently_invalidates_profile():
    frames = task_frames()
    for n in range(4, len(frames)):
        frames[n].image = image("OUTSIDE", ref_shift=20)
    result = analyze_frames(frames, profile())
    assert result["detected_cycles"] == []
    assert "CAMERA_MOVED" in result["quality_flags"]
    assert "PROFILE_INVALIDATED" in result["quality_flags"]


def test_resolution_change_invalidates_profile():
    frames = task_frames()
    frames[6].image = np.zeros((120, 160, 3), np.uint8)
    result = analyze_frames(frames, profile())
    assert "PROFILE_INVALIDATED" in result["quality_flags"]
    assert not result["detected_cycles"]


def test_motion_open_activity_never_finalizes_before_quiet_window():
    detector = MotionCycleDetector()
    img = np.zeros((120, 160, 3), np.uint8)
    for n in range(20):
        events, _, _ = detector.process(Frame(n, n * 100.0, img), 10)
        assert not events
    assert detector.completed == 0
    assert detector.finish(Frame(20, 2000.0, img))[0]["status"] == "PARTIAL"


def test_live_count_cumulative_and_state_bounded():
    source = SyntheticLiveSource([])
    engine = LiveEngine(
        source,
        EventBus(),
        merge_gap_ms=100,
        refractory_ms=100,
        min_active_ms=200,
        enter_score=1,
        exit_score=0.5,
    )
    img = np.zeros((120, 160, 3), np.uint8)
    for n in range(2000):
        phase = n % 20
        changed = img.copy()
        if phase < 8:
            changed[:, :] = (n % 2) * 255
        engine._process_image(changed, n * 100.0)
    assert engine.completed_cycles >= 95
    assert len(engine.pipeline.cycles) <= 128
    assert len(engine.pipeline.events) <= 128
    assert len(engine.bus.recent(1000)) <= 500
    engine.stop()


def test_lossless_segments_replay_exact_pixels_clocks_and_cycles(tmp_path):
    frames = task_frames()
    archive = recorder(tmp_path, segment_ms=500)
    live = MeasurementPipeline(profile())
    for frame in frames:
        archive.consume(frame)
        live.consume(frame)
    manifest = archive.stop()
    assert len(manifest["segments"]) == 4
    # Use recorded end for both paths: stop time is explicitly not nominal-FPS time.
    live.finish(manifest["end_ms"])
    replay = analyze_video(archive.manifest_path, profile())
    assert replay["detected_cycles"] == live.result()["detected_cycles"]
    assert replay["events"] == live.result()["events"]
    assert replay["coverage"] == live.result()["coverage"]
    decoded = []
    for segment in manifest["segments"]:
        source = VideoFileSource(tmp_path / segment["file"])
        decoded += list(source.frames())
        source.close()
    assert [(f.frame_no, f.ms, pixel_hash(f.image)) for f in decoded] == [
        (f.frame_no, f.ms, pixel_hash(f.image)) for f in frames
    ]


def test_recorded_clock_dictionary_is_indexed_as_data(tmp_path):
    archive = recorder(tmp_path)
    for frame in task_frames()[:4]:
        archive.consume(frame)
    manifest = archive.stop()
    path = tmp_path / manifest["segments"][0]["file"]
    source = VideoFileSource(path)
    assert [f.ms for f in source.frames()] == [0, 100, 200, 300]
    source.close()


@pytest.mark.parametrize(
    "rows",
    [
        [{"frame": 0, "ms": 0}, {"frame": 2, "ms": 100}],
        [{"frame": 0, "ms": 0}, {"frame": 1, "ms": 0}],
        [{"frame": 0, "ms": float("nan")}],
        [],
    ],
)
def test_invalid_frame_clocks_rejected(tmp_path, rows):
    clock = tmp_path / "frame_index.jsonl"
    clock.write_text("\n".join(json.dumps(r) for r in rows))
    with pytest.raises(ValueError):
        _load_clock(clock)


def test_partial_clock_rejected_not_fps_fallback(tmp_path):
    archive = recorder(tmp_path)
    for frame in task_frames()[:4]:
        archive.consume(frame)
    manifest = archive.stop()
    path = tmp_path / manifest["segments"][0]["file"]
    clock = path.with_suffix(".clock.jsonl")
    clock.write_text("\n".join(clock.read_text().splitlines()[:2]))
    source = VideoFileSource(path)
    with pytest.raises(ValueError, match="cover"):
        list(source.frames())
    source.close()


def test_capture_pixel_tamper_detected(tmp_path):
    archive = recorder(tmp_path)
    archive.consume(task_frames()[0])
    manifest = archive.stop()
    path = tmp_path / manifest["segments"][0]["file"]
    clock = path.with_suffix(".clock.jsonl")
    row = json.loads(clock.read_text())
    row["pixel_sha256"] = "0" * 64
    clock.write_text(json.dumps(row) + "\n")
    source = VideoFileSource(path)
    with pytest.raises(ValueError, match="pixels"):
        list(source.frames())
    source.close()


def test_disk_floor_stops_archive_preserving_prior_segment(tmp_path):
    free = [100000]
    archive = SegmentedRecorder(
        tmp_path, min_start_bytes=0, min_remaining_bytes=100, free_space=lambda: free[0]
    )
    archive.consume(task_frames()[0])
    free[0] = 99
    with pytest.raises(RecordingUnavailable, match="disk"):
        archive.consume(task_frames()[1])
    manifest = archive.stop()
    assert manifest["status"] == "interrupted"
    assert manifest["unavailable_since_ms"] == 100
    assert (tmp_path / manifest["segments"][0]["file"]).is_file()


def test_start_rejects_low_disk_without_capture(tmp_path):
    with pytest.raises(RecordingUnavailable):
        SegmentedRecorder(tmp_path, free_space=lambda: 0)


def test_out_of_order_observations_rejected():
    pipeline = MeasurementPipeline()
    frame = task_frames()[0]
    pipeline.consume(frame)
    with pytest.raises(ValueError, match="increasing"):
        pipeline.consume(frame)


def test_empty_analysis_rejected():
    with pytest.raises(ValueError, match="empty"):
        analyze_frames([])


def test_no_silent_synthetic_source_fallback():
    with pytest.raises(ValueError, match="explicit"):
        make_source()


def test_marker_detector_timeout_keeps_partial():
    cfg = profile()
    cfg["detector"]["max_cycle_ms"] = 500
    detector = MarkerTaskDetector(cfg)
    events = []
    for frame in task_frames()[:12]:
        new, _, _ = detector.process(frame)
        events += new
    assert any(e.get("reason") == "CYCLE_TIMEOUT" for e in events)
    assert detector.completed == 0


def test_uploaded_vfr_uses_decoded_pts_without_nominal_fps_fallback(tmp_path):
    from fractions import Fraction

    import av

    path = tmp_path / "vfr.mkv"
    container = av.open(str(path), "w")
    stream = container.add_stream("ffv1", rate=30)
    stream.width, stream.height = 320, 240
    stream.pix_fmt = "bgr0"
    stream.time_base = Fraction(1, 1000)
    stream.codec_context.time_base = Fraction(1, 1000)
    for ms in (1200, 1240, 1320, 1430):
        frame = av.VideoFrame.from_ndarray(image(), format="bgr24")
        frame.pts, frame.time_base = ms, Fraction(1, 1000)
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    source = VideoFileSource(path)
    observations = list(source.frames())
    source.close()
    assert [f.ms for f in observations] == pytest.approx([0, 40, 120, 230])
    assert all(f.clock_basis == "decoded_pts" for f in observations)


def test_missing_clock_records_without_decoded_frames_rejected(tmp_path):
    archive = recorder(tmp_path)
    archive.consume(task_frames()[0])
    manifest = archive.stop()
    path = tmp_path / manifest["segments"][0]["file"]
    clock = path.with_suffix(".clock.jsonl")
    original = json.loads(clock.read_text())
    extra = {**original, "frame": 1, "segment_frame": 1, "ms": 100}
    clock.write_text(clock.read_text() + json.dumps(extra) + "\n")
    source = VideoFileSource(path)
    with pytest.raises(ValueError, match="without decoded"):
        list(source.frames())
    source.close()


def test_missing_frame_numbers_break_cycle_and_are_unknown():
    frames = task_frames()
    for frame in frames[6:]:
        frame.frame_no += 1
    result = analyze_frames(frames, profile())
    assert result["detected_cycles"] == []
    assert "OBSERVATION_GAP" in result["quality_flags"]
    assert result["coverage"]["unknown_ms"] > 0


def test_config_hash_changes_with_explicit_motion_parameters():
    a = MeasurementPipeline(enter_score=4)
    b = MeasurementPipeline(enter_score=5)
    assert (
        a.result()["provenance"]["configuration_hash"]
        != b.result()["provenance"]["configuration_hash"]
    )


def test_deterministic_analysis_hash_and_provenance(tmp_path):
    archive = recorder(tmp_path)
    for frame in task_frames():
        archive.consume(frame)
    archive.stop()
    a = analyze_video(archive.manifest_path, profile())
    b = analyze_video(archive.manifest_path, profile())
    assert a["analysis_hash"] == b["analysis_hash"]
    assert a["provenance"]["observed_fps"] >= 10
    assert a["provenance"]["physically_qualified"] is False
    assert len(a["provenance"]["video_sha256"]) == 64


def test_engine_records_the_same_injected_observations_once(tmp_path):
    class Source:
        def __init__(self):
            self.reads, self.closed = 0, False

        def read(self):
            self.reads += 1
            raise AssertionError("injected process must not acquire a second frame")

        def close(self):
            self.closed = True

    source = Source()
    engine = LiveEngine(
        source, EventBus(), profile=profile(), session_dir=tmp_path, session_id="session"
    )
    for frame in task_frames():
        engine._process_image(frame.image, frame.ms)
    preview = engine.preview_jpeg()
    engine.stop()
    assert preview and preview[:2] == b"\xff\xd8"
    assert source.closed and source.reads == 0
    replay = analyze_video(tmp_path / "video" / "recording.manifest.json", profile())
    assert replay["detected_cycles"] == engine.pipeline.result()["detected_cycles"]
    assert replay["completed_cycles"] == engine.completed_cycles == 1
    persisted = [
        json.loads(line) for line in (tmp_path / "live-events.jsonl").read_text().splitlines()
    ]
    assert sum(e["type"] == "cycle_completed" for e in persisted) == 1


def test_trailing_camera_loss_is_unknown_instead_of_invented_idle():
    pipeline = MeasurementPipeline(profile())
    for frame in task_frames()[:4]:
        pipeline.consume(frame)
    pipeline.finish(2000)
    result = pipeline.result()
    assert result["coverage"]["unknown_ms"] == 1700
    assert "TRAILING_OBSERVATION_GAP" in result["quality_flags"]
    assert result["coverage"]["observed_ms"] + result["coverage"]["unknown_ms"] == 2000


def test_interrupted_capture_closes_open_segment_without_losing_prior_evidence(tmp_path):
    archive = recorder(tmp_path)
    archive.consume(task_frames()[0])
    archive.status = "interrupted"
    archive.error = "source failed"
    manifest = archive.stop(1000)
    assert manifest["status"] == "interrupted"
    assert len(manifest["segments"]) == 1
    assert manifest["end_ms"] == 1000
    result = analyze_video(archive.manifest_path, profile())
    assert "RECORDING_INTERRUPTED" in result["quality_flags"]
    assert result["coverage"]["unknown_ms"] == 1000


def test_scripted_demo_detects_independent_schedule_without_physical_claim(tmp_path):
    from tools.generate_demo import generate_demo

    demo = generate_demo(tmp_path, cycles=3, lossless=True)
    station = json.loads(Path(demo["profile"]).read_text())
    reference = [json.loads(line) for line in Path(demo["reference"]).read_text().splitlines()]
    manifest = json.loads((tmp_path / "dataset.manifest.json").read_text())
    result = analyze_video(Path(demo["video"]), station)
    assert result["completed_cycles"] == len(reference) == 3
    for expected, actual in zip(reference, result["detected_cycles"]):
        assert actual["start_ms"] == pytest.approx(expected["cycle_start_ms"], abs=1)
        assert actual["end_ms"] == pytest.approx(expected["cycle_end_ms"], abs=1)
    lossless_result = analyze_video(Path(demo["lossless_manifest"]), station)
    assert lossless_result["completed_cycles"] == 3
    assert manifest["source_kind"] == demo["source_kind"] == "simulation"
    assert manifest["physically_qualified"] is demo["physically_qualified"] is False
    assert not manifest["capture_attested_by"]
    assert manifest["annotator_kinds"] == {"scripted_sequence": "script"}
    assert all(row["basis"] == "GENERATION_SCHEDULE" for row in reference)
