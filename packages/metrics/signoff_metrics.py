"""Pure camera measurements; reference labels never alter operational numbers.

Outcome rows are human reviews, not detector hypotheses. Version-1 callers may
still supply gt_cycles but these are deliberately not used as observations.
"""
from __future__ import annotations

import math
import statistics


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100.0
    lower = int(k)
    upper = min(lower + 1, len(ordered) - 1)
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * (k - lower))


def completed_cycles(cycles: list[dict]) -> list[dict]:
    """Filter open/partial observations; reject malformed finalized boundaries."""
    result = []
    for cycle in cycles:
        status = str(cycle.get("status", "complete")).lower()
        if status not in ("complete", "completed", "finalized") or cycle.get("end_ms") is None:
            continue
        start, end = float(cycle["start_ms"]), float(cycle["end_ms"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("completed cycle must have finite, increasing nonnegative boundaries")
        result.append(cycle)
    return result


def cycle_durations(cycles: list[dict]) -> dict:
    durs = [float(c["end_ms"]) - float(c["start_ms"]) for c in completed_cycles(cycles)]
    if not durs:
        return {"n": 0}
    return {
        "n": len(durs),
        "mean_ms": round(statistics.fmean(durs), 1),
        "p50_ms": round(_percentile(durs, 50), 1),
        "p95_ms": round(_percentile(durs, 95), 1),
        "min_ms": round(min(durs), 1),
        "max_ms": round(max(durs), 1),
    }


def outcome_summary(cycles: list[dict]) -> dict:
    """Summarize reviewed outcomes; missing/UNKNOWN labels are never successful."""
    total = len(cycles)
    ok = sum(c.get("outcome") == "OK" and c.get("reviewed", True) for c in cycles)
    failed = sum(c.get("outcome") == "FAILED" and c.get("reviewed", True) for c in cycles)
    reviewed = sum(c.get("outcome") in ("OK", "FAILED", "UNKNOWN")
                   and c.get("reviewed", True) for c in cycles)
    confirmed = ok + failed
    breakdown: dict[str, int] = {}
    for cycle in cycles:
        if cycle.get("outcome") == "FAILED" and cycle.get("reviewed", True):
            kind = cycle.get("failure_type") or "UNSPECIFIED"
            breakdown[kind] = breakdown.get(kind, 0) + 1
    return {
        "total": total,
        "ok": ok,
        "failed": failed,
        "unknown": total - confirmed,
        "unreviewed": total - reviewed,
        "reviewed": reviewed,
        "confirmed": confirmed,
        "review_coverage": round(reviewed / total, 4) if total else None,
        "success_rate": round(ok / confirmed, 4) if confirmed else None,
        "available": bool(confirmed),
        "reason": None if confirmed else "no_confirmed_outcomes",
        "failure_breakdown": breakdown,
        "source": "human_review",
    }


def throughput(cycles: list[dict], session_span_ms: float | None = None) -> dict:
    """Use full session duration when supplied; retain labeled legacy fallback."""
    cycles = completed_cycles(cycles)
    span = session_span_ms
    basis = "full_session" if span is not None else "legacy_cycle_span"
    if span is None and cycles:
        span = max(c["end_ms"] for c in cycles) - min(c["start_ms"] for c in cycles)
    if span is None:
        return {"cycles_per_min": 0.0, "duration_ms": None, "duration_basis": basis,
                "available": False, "reason": "session_duration_unavailable"}
    if not math.isfinite(span) or span < 0:
        raise ValueError("session duration must be finite and nonnegative")
    if span == 0:
        return {"cycles_per_min": None, "duration_ms": span, "duration_basis": basis,
                "available": False, "reason": "zero_session_duration"}
    return {"cycles_per_min": round(len(cycles) / (span / 60000.0), 3),
            "duration_ms": span, "duration_basis": basis, "available": True,
            "source": "detected_cycles"}


def _union(intervals: list[dict], duration_ms: float) -> list[tuple[float, float]]:
    bounds = []
    for row in intervals:
        if row.get("end_ms") is None:
            continue
        start, end = float(row["start_ms"]), float(row["end_ms"])
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ValueError("interval boundaries must be finite and ordered")
        start, end = max(0.0, start), min(duration_ms, end)
        if end > start:
            bounds.append((start, end))
    merged: list[tuple[float, float]] = []
    for start, end in sorted(bounds):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _length(intervals: list[tuple[float, float]]) -> float:
    return sum(end - start for start, end in intervals)


def _overlap(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(ae, be) - max(ast, bst))
               for ast, ae in a for bst, be in b)


def idle_ratio(idle_intervals: list[dict], session_span_ms: float) -> dict:
    """Union/clamp intervals so overlaps cannot create more than 100% idle."""
    if not math.isfinite(session_span_ms) or session_span_ms < 0:
        raise ValueError("session duration must be finite and nonnegative")
    total_idle = _length(_union(idle_intervals, session_span_ms))
    return {"idle_ratio": round(total_idle / session_span_ms, 4) if session_span_ms else 0.0,
            "idle_total_ms": round(total_idle, 1)}


def observation_partition(idle_intervals: list[dict], duration_ms: float | None,
                          coverage: dict | None, unknown_intervals: list[dict]) -> dict:
    """Unknown has precedence over idle; remaining observed time is activity.

    Aggregate-only coverage cannot locate gaps, so interval partitioning stays
    unavailable rather than guessing how unknown time overlaps idle.
    """
    if duration_ms is None:
        return {"idle_ratio": None, "idle_total_ms": None, "activity_total_ms": None,
                "unknown_total_ms": None, "coverage": None, "partition_available": False,
                "reason": "session_duration_unavailable"}
    idle = _union(idle_intervals, duration_ms)
    unknown = _union(unknown_intervals, duration_ms)
    if coverage is None:
        return {**idle_ratio(idle_intervals, duration_ms), "activity_total_ms": None,
                "unknown_total_ms": None, "coverage": None, "partition_available": False,
                "reason": "observation_coverage_unavailable"}
    ratio = coverage.get("usable_ratio", coverage.get("ratio", coverage.get("coverage_ratio")))
    unknown_ms = coverage.get("unknown_ms", coverage.get("unknown_total_ms"))
    if unknown_ms is None and coverage.get("observed_ms") is not None:
        unknown_ms = duration_ms - float(coverage["observed_ms"])
    if unknown_ms is None and ratio is not None:
        unknown_ms = duration_ms * (1 - float(ratio))
    if unknown_ms is None:
        unknown_ms = _length(unknown)
    unknown_ms = float(unknown_ms)
    if not math.isfinite(unknown_ms) or not 0 <= unknown_ms <= duration_ms:
        raise ValueError("coverage unknown duration must be within the session")
    unknown_len = _length(unknown)
    if abs(unknown_ms - unknown_len) > 1.0:
        return {**idle_ratio(idle_intervals, duration_ms), "activity_total_ms": None,
                "unknown_total_ms": round(unknown_ms, 1), "coverage": coverage,
                "partition_available": False, "reason": "unknown_intervals_unavailable"}
    idle_ms = _length(idle) - _overlap(idle, unknown)
    activity_ms = max(0.0, duration_ms - unknown_len - idle_ms)
    return {"idle_ratio": round(idle_ms / duration_ms, 4) if duration_ms else None,
            "idle_total_ms": round(idle_ms, 1), "activity_total_ms": round(activity_ms, 1),
            "unknown_total_ms": round(unknown_len, 1),
            "coverage": {**coverage, "observed_ms": duration_ms - unknown_len,
                         "unknown_ms": unknown_len,
                         "usable_ratio": (duration_ms - unknown_len) / duration_ms
                         if duration_ms else None},
            "partition_available": True, "source": "visual_observation"}


def detection_quality(report: dict | None) -> dict:
    if not report:
        return {"available": False}
    keys = ("true_positives", "false_positives", "false_negatives", "precision", "recall", "f1",
            "matcher", "tolerance_ms", "timing_errors", "count_error", "uncertainty",
            "reference_id", "reference_revision", "reason", "matches")
    available = report.get("available", True)
    if report.get("ground_truth_cycles") == 0 and not report.get("reference_id"):
        available = False
    values = {key: report.get(key) for key in keys if key in report}
    if not available:
        values.update({key: None for key in ("precision", "recall", "f1")})
        values.setdefault("reason", "reference_annotations_unavailable")
    return {"available": bool(available), **values, "source": "reference_evaluation"}


def _reviewed_observations(cycles: list[dict], reviews: list[dict]) -> list[dict]:
    by_id = {}
    for row in reviews:
        identity = row.get("cycle_id", row.get("cycle"))
        if identity is not None:
            by_id[str(identity)] = row
    result = []
    for index, cycle in enumerate(cycles, 1):
        identity = cycle.get("cycle_id", cycle.get("cycle", index))
        review = by_id.get(str(identity))
        result.append({"outcome": review.get("outcome", "UNKNOWN") if review else "UNKNOWN",
                       "failure_type": review.get("failure_type", "NONE") if review else "NONE",
                       "reviewed": review is not None})
    return result


def session_metrics(gt_cycles: list[dict], detected_cycles: list[dict],
                    idle_intervals: list[dict], vision_report: dict | None = None, *,
                    session_duration_ms: float | None = None,
                    reviewed_cycles: list[dict] | None = None,
                    coverage: dict | None = None,
                    unknown_intervals: list[dict] | None = None) -> dict:
    """Version-2 semantics with the version-1 positional calling convention.

    gt_cycles is retained only for source compatibility and ignored. Missing
    full duration stays unavailable; references never supply the denominator.
    """
    cycles = completed_cycles(detected_cycles)
    if session_duration_ms is not None:
        session_duration_ms = float(session_duration_ms)
        if not math.isfinite(session_duration_ms) or session_duration_ms < 0:
            raise ValueError("session duration must be finite and nonnegative")
        if any(c["end_ms"] > session_duration_ms + 1.0 for c in cycles):
            raise ValueError("completed cycle extends beyond the measured session duration")
    durations = cycle_durations(cycles)
    durations.update({"partial_count": len(detected_cycles) - len(cycles),
                      "source": "detected_cycles"})
    thr = throughput(cycles, session_duration_ms)
    if session_duration_ms is None:
        thr.update({"cycles_per_min": None, "duration_ms": None, "available": False,
                    "duration_basis": "unavailable", "reason": "session_duration_unavailable"})
    return {
        "cycle_durations": durations,
        "outcomes": outcome_summary(_reviewed_observations(cycles, reviewed_cycles or [])),
        "throughput": thr,
        "idle": observation_partition(idle_intervals, session_duration_ms,
                                      coverage, unknown_intervals or []),
        "detection": detection_quality(vision_report),
    }
