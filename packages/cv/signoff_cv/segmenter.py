"""Cycle segmentation from the motion signal.

Hysteresis thresholding (enter above `enter_score`, exit below `exit_score`)
with minimum activity duration and a refractory window, then merge of
near-adjacent active stretches. Deterministic end-to-end.
"""
from __future__ import annotations

from dataclasses import dataclass

from packages.cv.signoff_cv.motion import MotionSignal


@dataclass
class DetectedInterval:
    start_ms: float
    end_ms: float


def segment_cycles(signals: list[MotionSignal],
                   enter_score: float = 4.0,
                   exit_score: float = 2.0,
                   min_active_ms: float = 400.0,
                   refractory_ms: float = 500.0,
                   merge_gap_ms: float = 300.0) -> list[DetectedInterval]:
    active: list[DetectedInterval] = []
    cur_start: float | None = None
    last_active_ms: float | None = None

    for sig in signals:
        if sig.score >= enter_score:
            if cur_start is None:
                # respect refractory window after previous interval's end
                if not active or sig.ms - active[-1].end_ms >= refractory_ms:
                    cur_start = sig.ms
        elif cur_start is not None and sig.score < exit_score:
            assert last_active_ms is not None
            if sig.ms - last_active_ms > min_active_ms:
                pass  # duration checked at close time below
            active.append(DetectedInterval(cur_start, sig.ms))
            cur_start = None
        if sig.score >= exit_score:
            last_active_ms = sig.ms

    # still open at stream end -> close on last signal
    if cur_start is not None and signals:
        active.append(DetectedInterval(cur_start, signals[-1].ms))

    # drop too-short intervals
    active = [iv for iv in active if iv.end_ms - iv.start_ms >= min_active_ms]

    return _merge(active, merge_gap_ms)


def detect_idle(signals: list[MotionSignal],
                idle_below: float = 1.0,
                min_idle_ms: float = 1000.0) -> list[DetectedInterval]:
    """Sustained below-threshold stretches = idle episodes."""
    idles: list[DetectedInterval] = []
    start: float | None = None
    for sig in signals:
        if sig.score < idle_below:
            if start is None:
                start = sig.ms
        else:
            if start is not None and sig.ms - start >= min_idle_ms:
                idles.append(DetectedInterval(start, sig.ms))
            start = None
    if start is not None and signals:
        end_ms = signals[-1].ms
        if end_ms - start >= min_idle_ms:
            idles.append(DetectedInterval(start, end_ms))
    return idles


def _merge(intervals: list[DetectedInterval],
           gap_ms: float) -> list[DetectedInterval]:
    if not intervals:
        return []
    out = [intervals[0]]
    for iv in intervals[1:]:
        prev = out[-1]
        if iv.start_ms - prev.end_ms <= gap_ms:
            prev.end_ms = max(prev.end_ms, iv.end_ms)
        else:
            out.append(iv)
    return out
