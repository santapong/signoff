"""Phase 2 CV pipeline tests: synthetic frames, deterministic end-to-end."""
from __future__ import annotations

import json

import numpy as np

from packages.cv.signoff_cv.evaluate import GroundTruthCycle, load_ground_truth_cycles, match_cycles
from packages.cv.signoff_cv.motion import MotionDetector, signals_from_frames
from packages.cv.signoff_cv.segmenter import detect_idle, segment_cycles
from packages.cv.signoff_cv.source import Frame, SyntheticSource

FPS = 30.0


def _blank() -> np.ndarray:
    return np.zeros((120, 160, 3), dtype=np.uint8)


def _with_square(img: np.ndarray, jitter: int = 0) -> np.ndarray:
    out = img.copy()
    y = 40 + (jitter * 7) % 20
    x = 60 - (jitter * 5) % 15
    out[y:y + 40, x:x + 40] = (255, 255, 255)
    return out


def _synthetic_session(cycles_ms, total_s: float) -> SyntheticSource:
    """Background with a jittering bright square during activity windows.

    The square moves slightly each frame so consecutive active frames differ
    (like a real arm moving), not just on appear/disappear transitions.
    """
    total_frames = int(total_s * FPS)
    images = []
    for n in range(total_frames):
        ms = n / FPS * 1000.0
        active = any(s <= ms <= e for s, e in cycles_ms)
        images.append(_with_square(_blank(), jitter=n) if active else _blank())
    return SyntheticSource(images, cycles_ms)


class TestMotion:
    def test_static_is_zero_after_first_frame(self):
        frames = [Frame(i, i / FPS * 1000, _blank()) for i in range(20)]
        signals = signals_from_frames(frames)
        assert all(s.score == 0.0 for s in signals)

    def test_activity_spikes(self):
        frames = [
            Frame(0, 0.0, _blank()),
            Frame(1, 33.3, _with_square(_blank())),
            Frame(2, 66.6, _with_square(_blank())),
            Frame(3, 100.0, _blank()),
        ]
        signals = signals_from_frames(frames)
        assert signals[0].score == 0.0
        assert signals[1].score > 10.0   # square appeared
        assert signals[2].score == 0.0   # unchanged
        assert signals[3].score > 10.0   # square vanished

    def test_deterministic(self):
        frames = [Frame(i, float(i), _with_square(_blank()) if i % 2 else _blank())
                  for i in range(30)]
        a = signals_from_frames(frames)
        b = signals_from_frames(frames)
        assert [(s.frame_no, s.ms, s.score) for s in a] == \
               [(s.frame_no, s.ms, s.score) for s in b]


class TestSegmenter:
    def test_finds_exact_n_cycles(self):
        # three activity windows of 1.5s separated by 2s idle
        cycles = [(1000, 2500), (4500, 6000), (8000, 9500)]
        src = _synthetic_session(cycles, total_s=11.0)
        det = MotionDetector()
        signals = [det.process(f) for f in src.frames()]
        found = segment_cycles(signals, enter_score=4.0, exit_score=2.0,
                               min_active_ms=400.0, refractory_ms=500.0,
                               merge_gap_ms=300.0)
        assert len(found) == 3
        for got, (want_s, want_e) in zip(found, cycles):
            assert abs(got.start_ms - want_s) < 200.0, (got, want_s)
            assert abs(got.end_ms - want_e) < 200.0, (got, want_e)

    def test_short_blinks_filtered(self):
        # 150ms blinks below min_active_ms must not become cycles
        cycles = [(1000, 1150)]
        src = _synthetic_session(cycles, total_s=3.0)
        det = MotionDetector()
        signals = [det.process(f) for f in src.frames()]
        found = segment_cycles(signals, min_active_ms=400.0)
        assert found == []

    def test_idle_episodes(self):
        cycles = [(2000, 3500)]
        src = _synthetic_session(cycles, total_s=8.0)
        det = MotionDetector()
        signals = [det.process(f) for f in src.frames()]
        idles = detect_idle(signals, idle_below=1.0, min_idle_ms=1000.0)
        assert idles, "expected idle stretches around the single cycle"
        # first idle covers the pre-cycle lead-in
        assert idles[0].start_ms < 500.0


class TestEvaluation:
    def gt(self):
        return [
            GroundTruthCycle(1, start_ms=1000, end_ms=2500),
            GroundTruthCycle(2, start_ms=4500, end_ms=6000),
        ]

    def test_perfect_match_f1_one(self):
        rep = match_cycles(self.gt(), [(1010, 2490), (4480, 6010)])
        d = rep.to_dict()
        assert d["f1"] == 1.0
        assert d["false_positives"] == 0 and d["false_negatives"] == 0

    def test_within_tolerance_counts(self):
        rep = match_cycles(self.gt(), [(1300, 2400)], tolerance_ms=500.0)
        assert rep.true_positives == 1 and rep.false_negatives == 1

    def test_outside_tolerance_misses(self):
        rep = match_cycles(self.gt(), [(1900, 2400)], tolerance_ms=500.0)
        assert rep.false_positives == 1 and rep.false_negatives == 2

    def test_extra_detection_false_positive(self):
        rep = match_cycles(self.gt(), [(1010, 2400), (4490, 5900), (9000, 9500)])
        d = rep.to_dict()
        assert d["true_positives"] == 2 and d["false_positives"] == 1

    def test_empty_inputs_zero_division_safe(self):
        rep = match_cycles([], [])
        assert rep.f1 == 0.0


class TestGroundTruthLoader:
    def test_load_pairs_start_completed(self, tmp_path):
        events = tmp_path / "events.jsonl"
        rows = [
            {"event": "CYCLE_START", "cycle": 1, "ms": 100.0},
            {"event": "GRIPPER_CLOSED", "cycle": 1, "ms": 400.0},
            {"event": "CYCLE_COMPLETED", "cycle": 1, "ms": 900.0},
            {"event": "CYCLE_START", "cycle": 2, "ms": 1200.0},
            {"event": "FAULT_INJECTED", "cycle": 2, "fault": "interrupt",
             "ms": 1500.0},
        ]
        events.write_text("\n".join(json.dumps(r) for r in rows))
        cycles = load_ground_truth_cycles(events)
        assert len(cycles) == 1          # cycle 2 never completed -> excluded
        assert cycles[0].start_ms == 100.0 and cycles[0].end_ms == 900.0
