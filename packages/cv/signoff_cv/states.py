"""Cycle phase splitting + failure hypotheses (best-effort, INFERRED)."""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.cv.signoff_cv.motion import MotionSignal
from packages.cv.signoff_cv.segmenter import DetectedInterval


PHASES = ["APPROACH", "GRASP", "TRANSFER", "PLACE_RETRACT"]


@dataclass
class CyclePhases:
    cycle_index: int
    start_ms: float
    end_ms: float
    phases: list[dict] = field(default_factory=list)  # {name, start_ms, end_ms}
    basis: str = "INFERRED"  # protocol: never asserted fact


def split_phases(interval: DetectedInterval,
                 signals: list[MotionSignal],
                 cycle_index: int = 0) -> CyclePhases:
    """Cut the cycle into 4 phases by relative duration of the activity profile.

    Deterministic: cuts are at fixed fractions (25/50/75%) of the interval.
    """
    span = interval.end_ms - interval.start_ms
    if span <= 0:
        return CyclePhases(cycle_index, interval.start_ms, interval.end_ms)
    cuts = [interval.start_ms + span * f for f in (0.25, 0.5, 0.75)]
    bounds = [interval.start_ms, *cuts, interval.end_ms]
    phases = [
        {"name": name, "start_ms": round(bounds[i], 1),
         "end_ms": round(bounds[i + 1], 1)}
        for i, name in enumerate(PHASES)
    ]
    return CyclePhases(cycle_index, interval.start_ms, interval.end_ms, phases)


@dataclass
class FailureHypothesis:
    cycle_index: int
    failure_type: str          # domain FailureType vocabulary
    confidence: float          # 0..1 heuristic strength
    evidence: str
    basis: str = "INFERRED"


def hypothesize_failure(phases: CyclePhases,
                        activity_scores: list[float],
                        ground_truth_fault: str | None = None,
                        completed: bool = True) -> list[FailureHypothesis]:
    """Heuristic failure typing; ground-truth fault label boosts confidence.

    Shapes:
      - flat/low mid-cycle activity            -> GRASP_MISS (object never lifted)
      - truncated cycle (never completed)      -> INTERRUPTED
      - late activity burst after PLACE_RETRACT-> OBJECT_SLIP candidate
    """
    out: list[FailureHypothesis] = []
    n = len(activity_scores)
    if n < 4:
        return out

    def seg(a: int, b: int) -> float:
        chunk = activity_scores[max(0, a):min(b, n)]
        return sum(chunk) / len(chunk) if chunk else 0.0

    overall = seg(0, n)
    if overall <= 0:
        return out

    mid = seg(int(n * 0.35), int(n * 0.65))
    tail = seg(int(n * 0.85), n)

    if not completed:
        conf = 0.9 if ground_truth_fault in ("interrupt", None) else 0.7
        out.append(FailureHypothesis(
            phases.cycle_index, "INTERRUPTED", conf,
            "activity stream ends before cycle completion"))
        return out

    if mid < overall * 0.35:
        conf = 0.85 if ground_truth_fault == "grasp_miss" else 0.5
        out.append(FailureHypothesis(
            phases.cycle_index, "GRASP_MISS", conf,
            f"mid-cycle activity {mid:.1f} much below overall {overall:.1f}"))
        return out

    if tail > overall * 1.4:
        conf = 0.8 if ground_truth_fault in ("drop", None) else 0.45
        out.append(FailureHypothesis(
            phases.cycle_index, "OBJECT_SLIP", conf,
            f"tail activity {tail:.1f} spikes above overall {overall:.1f}"))

    return out


def needs_review(confidence: float, threshold: float = 0.75) -> bool:
    return confidence < threshold
