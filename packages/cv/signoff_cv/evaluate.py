"""E2 instrument: match detected cycles to ground truth, compute P/R/F1.

Ground truth comes from the Phase 1 hidden logger
(`datasets/sessions/<sid>/.ground_truth/events.jsonl`); detections come from
the segmenter. Matching is greedy one-to-one within a tolerance window.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MatchReport:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def to_dict(self) -> dict:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


@dataclass
class GroundTruthCycle:
    cycle: int
    start_ms: float
    end_ms: float | None = None
    outcome: str = "UNKNOWN"
    extra: dict = field(default_factory=dict)


def load_ground_truth_cycles(events_path: Path) -> list[GroundTruthCycle]:
    """Parse CYCLE_START / CYCLE_COMPLETED events from events.jsonl."""
    cycles: dict[int, GroundTruthCycle] = {}
    order: list[GroundTruthCycle] = []
    with Path(events_path).open() as fh:
        for line in fh:
            ev = json.loads(line)
            etype = ev.get("event")
            cyc = ev.get("cycle")
            ms = float(ev.get("ms", ev.get("t_ms", 0.0)))
            if etype == "CYCLE_START":
                c = GroundTruthCycle(cycle=cyc, start_ms=ms,
                                     outcome=ev.get("outcome", "UNKNOWN"),
                                     extra={k: v for k, v in ev.items()
                                            if k not in ("event", "cycle", "ms")})
                cycles[cyc] = c
                order.append(c)
            elif etype == "CYCLE_COMPLETED" and cyc in cycles:
                cycles[cyc].end_ms = ms
    return [c for c in order if c.end_ms is not None]


def match_cycles(ground_truth: list[GroundTruthCycle],
                 detected: list[tuple[float, float]],
                 tolerance_ms: float = 500.0) -> MatchReport:
    """Greedy nearest-start matching; a detection matches at most one GT cycle."""
    report = MatchReport()
    used_gt: set[int] = set()

    # sort detections by start for deterministic greedy assignment
    remaining = sorted(detected, key=lambda d: d[0])
    gt_sorted = sorted(range(len(ground_truth)),
                       key=lambda i: ground_truth[i].start_ms)

    for det_start, _det_end in remaining:
        best_i, best_dist = None, None
        for i in gt_sorted:
            if i in used_gt:
                continue
            dist = abs(ground_truth[i].start_ms - det_start)
            if best_dist is None or dist < best_dist:
                best_i, best_dist = i, dist
        if best_i is not None and best_dist <= tolerance_ms:
            used_gt.add(best_i)
            report.true_positives += 1
        else:
            report.false_positives += 1

    report.false_negatives = len(ground_truth) - len(used_gt)
    return report


def evaluate_session(session_dir: Path,
                     detected: list[tuple[float, float]],
                     tolerance_ms: float = 500.0) -> dict:
    """Full-session evaluation; writes vision_report.json next to the video."""
    session_dir = Path(session_dir)
    events = session_dir / ".ground_truth" / "events.jsonl"
    gt = load_ground_truth_cycles(events) if events.exists() else []
    report = match_cycles(gt, detected, tolerance_ms)
    out = {
        "session": session_dir.name,
        "tolerance_ms": tolerance_ms,
        "ground_truth_cycles": len(gt),
        "detected_cycles": len(detected),
        **report.to_dict(),
        "ground_truth_detail": [
            {"cycle": c.cycle, "start_ms": c.start_ms, "end_ms": c.end_ms,
             "outcome": c.outcome}
            for c in gt
        ],
    }
    dest = session_dir / "vision_report.json"
    dest.write_text(json.dumps(out, indent=2))
    return out
