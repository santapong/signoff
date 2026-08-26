"""Metrics engine (offline v0.1): pure functions over session artifacts.

Inputs are plain dicts/lists (already-parsed JSONL/JSON) so the module is
trivially testable; file loading lives in the API/reporting layer.
"""
from __future__ import annotations

import statistics


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return float(ordered[f])
    return float(ordered[f] + (ordered[c] - ordered[f]) * (k - f))


def cycle_durations(cycles: list[dict]) -> dict:
    """cycles: [{'start_ms': float, 'end_ms': float}, ...]"""
    durs = [c["end_ms"] - c["start_ms"] for c in cycles if c["end_ms"] > c["start_ms"]]
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
    """cycles with optional 'outcome' ('OK'/'FAILED') and 'failure_type'."""
    total = len(cycles)
    ok = sum(1 for c in cycles if c.get("outcome", "OK") == "OK")
    failed = total - ok
    breakdown: dict[str, int] = {}
    for c in cycles:
        ft = c.get("failure_type") or "NONE"
        if c.get("outcome", "OK") == "FAILED" or ft != "NONE":
            breakdown[ft] = breakdown.get(ft, 0) + 1
    return {
        "total": total,
        "ok": ok,
        "failed": failed,
        "success_rate": round(ok / total, 4) if total else 0.0,
        "failure_breakdown": breakdown,
    }


def throughput(cycles: list[dict], session_span_ms: float | None = None) -> dict:
    """Cycles per minute. Uses first start to last end unless a full span given."""
    if not cycles:
        return {"cycles_per_min": 0.0}
    span = session_span_ms
    if span is None:
        span = max(c["end_ms"] for c in cycles) - min(c["start_ms"] for c in cycles)
    if span <= 0:
        return {"cycles_per_min": 0.0}
    return {"cycles_per_min": round(len(cycles) / (span / 60000.0), 3)}


def idle_ratio(idle_intervals: list[dict], session_span_ms: float) -> dict:
    total_idle = sum(i["end_ms"] - i["start_ms"] for i in idle_intervals
                     if i["end_ms"] > i["start_ms"])
    if session_span_ms <= 0:
        return {"idle_ratio": 0.0, "idle_total_ms": 0.0}
    return {
        "idle_ratio": round(total_idle / session_span_ms, 4),
        "idle_total_ms": round(total_idle, 1),
    }


def detection_quality(report: dict | None) -> dict:
    """Pass through the Phase 2 vision report's match numbers, tolerating absence."""
    if not report:
        return {"available": False}
    keys = ("true_positives", "false_positives", "false_negatives",
            "precision", "recall", "f1")
    return {"available": True,
            **{k: report.get(k) for k in keys}}


def session_metrics(gt_cycles: list[dict],
                    detected_cycles: list[dict],
                    idle_intervals: list[dict],
                    vision_report: dict | None = None) -> dict:
    span = None
    all_ends = [c["end_ms"] for c in gt_cycles + detected_cycles]
    all_starts = [c["start_ms"] for c in gt_cycles + detected_cycles]
    if all_ends and max(all_ends) > min(all_starts):
        span = float(max(all_ends))
    return {
        "cycle_durations": cycle_durations(gt_cycles or detected_cycles),
        "outcomes": outcome_summary(gt_cycles),
        "throughput": throughput(gt_cycles or detected_cycles, span),
        "idle": idle_ratio(idle_intervals, span or 0.0),
        "detection": detection_quality(vision_report),
    }
