"""Post-hoc scoring, strictly separate from camera inference.

Qualification uses maximum-cardinality one-to-one matching with both boundaries
within tolerance and minimum summed boundary error. Historical start-only greedy
scores remain explicitly named. Confidence intervals use the Wilson score method:
https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from packages.metrics.signoff_metrics import _percentile


def wilson_interval(successes: int, total: int) -> dict:
    if total < 0 or not 0 <= successes <= total:
        raise ValueError("binomial counts must satisfy 0 <= successes <= total")
    if not total:
        return {"lower": None, "upper": None, "n": 0, "confidence": 0.95,
                "method": "wilson_score", "reason": "zero_denominator"}
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return {"lower": round(max(0.0, center - half), 6),
            "upper": round(min(1.0, center + half), 6), "n": total,
            "confidence": 0.95, "method": "wilson_score"}


def _error_summary(values: list[float]) -> dict:
    absolute = [abs(value) for value in values]
    return {"n": len(values),
            "mean_signed_ms": round(sum(values) / len(values), 4) if values else None,
            "p50_absolute_ms": round(_percentile(absolute, 50), 4) if values else None,
            "p95_absolute_ms": round(_percentile(absolute, 95), 4) if values else None,
            "max_absolute_ms": round(max(absolute), 4) if values else None}


@dataclass
class MatchReport:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    matches: list[dict] = field(default_factory=list)
    matcher: str = "two_boundary_max_cardinality_min_error"
    tolerance_ms: float = 500.0

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
        predicted = self.true_positives + self.false_positives
        references = self.true_positives + self.false_negatives
        return {
            "available": True,
            "matcher": self.matcher,
            "tolerance_ms": self.tolerance_ms,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4) if predicted else None,
            "recall": round(self.recall, 4) if references else None,
            "f1": round(self.f1, 4) if predicted or references else None,
            "matches": self.matches,
            "count_error": {
                "detected": predicted, "reference": references,
                "signed": predicted - references, "absolute": abs(predicted - references),
                "absolute_percent": round(abs(predicted - references) / references * 100, 4)
                if references else None},
            "timing_errors": {
                kind: _error_summary([m[f"{kind}_error_ms"] for m in self.matches])
                for kind in ("start", "end", "duration")},
            "uncertainty": {
                "precision": wilson_interval(self.true_positives, predicted),
                "recall": wilson_interval(self.true_positives, references),
                "note": "Cycle-level binomial intervals; session correlations are not modeled."},
        }


@dataclass
class GroundTruthCycle:
    cycle: int | str
    start_ms: float
    end_ms: float | None = None
    outcome: str = "UNKNOWN"
    extra: dict = field(default_factory=dict)


def load_ground_truth_cycles(events_path: Path) -> list[GroundTruthCycle]:
    cycles: dict[int | str, GroundTruthCycle] = {}
    order: list[GroundTruthCycle] = []
    for line in Path(events_path).read_text().splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        etype, cycle = ev.get("event"), ev.get("cycle")
        if etype not in ("CYCLE_START", "CYCLE_COMPLETED"):
            continue
        timestamp = next((ev[key] for key in ("ms", "t_ms", "timestamp_ms") if key in ev), None)
        if timestamp is None:
            raise ValueError("reference event has no timestamp")
        ms = float(timestamp)
        if not math.isfinite(ms) or ms < 0:
            raise ValueError("reference timestamp must be finite and nonnegative")
        if etype == "CYCLE_START":
            if cycle is None or cycle in cycles:
                raise ValueError("reference cycle identities must be present and unique")
            item = GroundTruthCycle(cycle, ms, outcome=ev.get("outcome", "UNKNOWN"))
            cycles[cycle] = item
            order.append(item)
        elif cycle in cycles:
            if cycles[cycle].end_ms is not None:
                raise ValueError("reference cycle completed more than once")
            cycles[cycle].end_ms = ms
    return [cycle for cycle in order if cycle.end_ms is not None]


def _references(rows: list) -> list[GroundTruthCycle]:
    result = []
    identities = set()
    for index, row in enumerate(rows):
        if isinstance(row, GroundTruthCycle):
            item = row
        elif isinstance(row, dict):
            item = GroundTruthCycle(row.get("cycle_id", row.get("cycle", index + 1)),
                                    row.get("start_ms", row.get("cycle_start_ms")),
                                    row.get("end_ms", row.get("cycle_end_ms")),
                                    row.get("outcome", "UNKNOWN"))
        else:
            item = GroundTruthCycle(index + 1, row[0], row[1])
        _validate_bounds(item.start_ms, item.end_ms)
        identity = str(item.cycle)
        if identity in identities:
            raise ValueError("reference cycle IDs must be unique")
        identities.add(identity)
        result.append(item)
    return sorted(result, key=lambda c: (str(c.cycle), c.start_ms, c.end_ms))


def _validate_bounds(start, end) -> None:
    if (start is None or end is None or not math.isfinite(float(start))
            or not math.isfinite(float(end)) or float(start) < 0 or float(end) <= float(start)):
        raise ValueError("cycle boundaries must be finite, nonnegative and increasing")


def _detections(rows: list) -> list[dict]:
    result = []
    identities = set()
    implicit = []
    for index, row in enumerate(rows):
        if isinstance(row, dict):
            start, end = row["start_ms"], row["end_ms"]
            identity = row.get("cycle_id", row.get("cycle"))
        else:
            start, end = row
            identity = None
        _validate_bounds(start, end)
        if identity is None:
            implicit.append((float(start), float(end)))
            continue
        identity = str(identity)
        if identity in identities:
            raise ValueError("detected cycle IDs must be unique")
        identities.add(identity)
        result.append({"id": identity, "start_ms": float(start), "end_ms": float(end)})
    for index, (start, end) in enumerate(sorted(implicit)):
        identity = f"det:{start}:{end}:{index}"
        if identity in identities:
            raise ValueError("detected cycle IDs must be unique")
        identities.add(identity)
        result.append({"id": identity, "start_ms": start, "end_ms": end})
    return sorted(result, key=lambda c: (c["id"], c["start_ms"], c["end_ms"]))


@dataclass
class _Edge:
    to: int
    reverse: int
    capacity: int
    cost: float


def _add_edge(graph: list[list[_Edge]], source: int, dest: int, cost: float) -> _Edge:
    forward = _Edge(dest, len(graph[dest]), 1, cost)
    reverse = _Edge(source, len(graph[source]), 0, -cost)
    graph[source].append(forward)
    graph[dest].append(reverse)
    return forward


def _min_cost_pairs(gt: list[GroundTruthCycle], detections: list[dict],
                    tolerance_ms: float) -> list[tuple[int, int]]:
    """Successive shortest augmenting paths on a sparse residual network.

    Augment until no path remains (maximum cardinality); residual reversals
    permit earlier assignments to change to minimize total boundary error.
    Sorted stable IDs and strict improvement comparisons resolve equal costs.
    """
    source, sink = len(gt) + len(detections), len(gt) + len(detections) + 1
    graph: list[list[_Edge]] = [[] for _ in range(sink + 1)]
    for i in range(len(gt)):
        _add_edge(graph, source, i, 0.0)
    for j in range(len(detections)):
        _add_edge(graph, len(gt) + j, sink, 0.0)
    candidates = []
    for i, reference in enumerate(gt):
        for j, detection in enumerate(detections):
            start_error = abs(float(reference.start_ms) - detection["start_ms"])
            end_error = abs(float(reference.end_ms) - detection["end_ms"])
            if start_error <= tolerance_ms and end_error <= tolerance_ms:
                edge = _add_edge(graph, i, len(gt) + j, start_error + end_error)
                candidates.append((i, j, edge))
    while True:
        distances = [math.inf] * len(graph)
        parent: list[tuple[int, int] | None] = [None] * len(graph)
        distances[source] = 0.0
        # Bellman-Ford handles negative-cost reverse edges without dependencies.
        for _ in range(len(graph) - 1):
            changed = False
            for node, edges in enumerate(graph):
                if distances[node] == math.inf or node == sink:
                    continue
                for index, edge in enumerate(edges):
                    cost = distances[node] + edge.cost
                    if edge.capacity and edge.to != source and cost < distances[edge.to]:
                        distances[edge.to] = cost
                        parent[edge.to] = (node, index)
                        changed = True
            if not changed:
                break
        if parent[sink] is None:
            break
        node = sink
        while node != source:
            previous, edge_index = parent[node]
            edge = graph[previous][edge_index]
            edge.capacity -= 1
            graph[node][edge.reverse].capacity += 1
            node = previous
    return [(i, j) for i, j, edge in candidates if edge.capacity == 0]


def match_cycles(ground_truth: list, detected: list,
                 tolerance_ms: float = 500.0) -> MatchReport:
    if not math.isfinite(tolerance_ms) or tolerance_ms < 0:
        raise ValueError("tolerance must be finite and nonnegative")
    gt, detections = _references(ground_truth), _detections(detected)
    pairs = _min_cost_pairs(gt, detections, tolerance_ms)
    matches = []
    for i, j in pairs:
        reference, detection = gt[i], detections[j]
        start_error = detection["start_ms"] - float(reference.start_ms)
        end_error = detection["end_ms"] - float(reference.end_ms)
        matches.append({"reference_cycle": reference.cycle, "detected_cycle_id": detection["id"],
                        "start_error_ms": start_error, "end_error_ms": end_error,
                        "duration_error_ms": end_error - start_error,
                        "boundary_absolute_error_ms": abs(start_error) + abs(end_error)})
    return MatchReport(len(pairs), len(detections) - len(pairs), len(gt) - len(pairs),
                       matches, tolerance_ms=tolerance_ms)


def match_cycles_legacy_start_only(ground_truth: list, detected: list,
                                   tolerance_ms: float = 500.0) -> MatchReport:
    """Historical nearest-start greedy score; never use for qualification."""
    gt, detections = _references(ground_truth), _detections(detected)
    report = MatchReport(matcher="legacy_start_only_greedy", tolerance_ms=tolerance_ms)
    used: set[int] = set()
    for detection in sorted(detections, key=lambda row: (row["start_ms"], row["id"])):
        candidates = [(abs(float(reference.start_ms) - detection["start_ms"]), i)
                      for i, reference in enumerate(gt) if i not in used]
        distance, index = min(candidates, default=(math.inf, -1))
        if distance <= tolerance_ms:
            used.add(index)
            report.true_positives += 1
        else:
            report.false_positives += 1
    report.false_negatives = len(gt) - len(used)
    return report


def evaluate_cycles(reference_cycles: list, detected_cycles: list,
                    tolerance_ms: float = 500.0, *, reference_id: str,
                    reference_revision: str | None = None) -> dict:
    """Pure identified-reference evaluation; writes no files and runs no inference."""
    if not reference_id:
        raise ValueError("reference_id is required for identified scoring")
    report = match_cycles(reference_cycles, detected_cycles, tolerance_ms).to_dict()
    report.update({"schema_version": 2, "reference_id": reference_id,
                   "reference_revision": reference_revision,
                   "ground_truth_cycles": len(reference_cycles),
                   "detected_cycles": len(detected_cycles),
                   "legacy_start_only": match_cycles_legacy_start_only(
                       reference_cycles, detected_cycles, tolerance_ms).to_dict()})
    return report


def evaluate_session(session_dir: Path, detected: list,
                     tolerance_ms: float = 500.0) -> dict:
    session_dir = Path(session_dir)
    events = session_dir / ".ground_truth" / "events.jsonl"
    if events.exists():
        gt = load_ground_truth_cycles(events)
        reference_hash = hashlib.sha256(events.read_bytes()).hexdigest()
        out = evaluate_cycles(gt, detected, tolerance_ms, reference_id=reference_hash)
        out["ground_truth_detail"] = [
            {"cycle": c.cycle, "start_ms": c.start_ms, "end_ms": c.end_ms, "outcome": c.outcome}
            for c in gt]
    else:
        out = {"schema_version": 2, "available": False,
               "reason": "reference_annotations_unavailable", "reference_id": None,
               "tolerance_ms": tolerance_ms, "ground_truth_cycles": None,
               "detected_cycles": len(detected), "ground_truth_detail": [],
               **{key: None for key in ("true_positives", "false_positives", "false_negatives",
                                        "precision", "recall", "f1")}}
    out["session"] = session_dir.name
    (session_dir / "vision_report.json").write_text(json.dumps(out, indent=2))
    return out
