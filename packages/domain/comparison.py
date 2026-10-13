"""Secondary experiment adapter: compare frozen camera reports, never run robots."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from pathlib import Path

from packages.reporting.signoff_report import _csv_safe, _publish_immutable, verify_report


def build_comparison(entries: list[dict], *, protocol_id: str, protocol_hash: str) -> dict:
    """Entries contain report plus experiment_id/controller_label/policy_label.

    Reports must use the same declared camera protocol and compatible known
    instrument provenance. Unknown provenance/physical qualification produces a
    provisional comparison, preserving utility without a qualification claim.
    """
    if len(entries) < 2:
        raise ValueError("at least two reports are required")
    if not protocol_id.strip() or not protocol_hash.strip():
        raise ValueError("a fixed protocol ID and hash are required")
    rows = []
    fingerprints: dict[str, set] = {
        key: set() for key in ("profile_hash", "configuration_hash", "timestamp_basis", "code_sha")}
    missing = set()
    seen = set()
    provisional_reasons = set()
    for entry in entries:
        report = copy.deepcopy(entry["report"])
        if not verify_report(report):
            raise ValueError("comparison requires an intact schema-v2 report")
        experiment_id = str(entry.get("experiment_id", "")).strip()
        controller = str(entry.get("controller_label", "")).strip()
        policy = str(entry.get("policy_label", "")).strip()
        if not experiment_id or not (controller or policy):
            raise ValueError("experiment ID and controller or policy label are required")
        if entry.get("protocol_id", protocol_id) != protocol_id:
            raise ValueError("comparison protocol ID mismatch")
        if entry.get("protocol_hash", protocol_hash) != protocol_hash:
            raise ValueError("comparison protocol hash mismatch")
        identity = report["report_hash"]
        if identity in seen:
            raise ValueError("the same measurement report cannot be compared twice")
        seen.add(identity)
        for key in fingerprints:
            value = report["provenance"].get(key)
            if value is None:
                missing.add(key)
            else:
                fingerprints[key].add(json.dumps(value, sort_keys=True))
        qualification = report.get("qualification", {})
        if (str(qualification.get("status", "")).lower() not in ("qualified", "passed")
                or qualification.get("physical") is not True
                or not qualification.get("receipt_hash")):
            provisional_reasons.add("physical_qualification_receipt_unavailable")
        metrics = report["metrics"]
        rows.append({
            "experiment_id": experiment_id,
            "controller_label": controller or None,
            "policy_label": policy or None,
            "session_id": report["session"].get("session_id"),
            "run_id": report.get("run_id"),
            "report_hash": report["report_hash"],
            "profile_hash": report["provenance"].get("profile_hash"),
            "qualification": qualification,
            "cycle_count": metrics.get("cycle_durations", {}).get("n", 0),
            "mean_cycle_ms": metrics.get("cycle_durations", {}).get("mean_ms"),
            "p95_cycle_ms": metrics.get("cycle_durations", {}).get("p95_ms"),
            "throughput_cycles_per_min": metrics.get("throughput", {}).get("cycles_per_min"),
            "session_duration_ms": metrics.get("throughput", {}).get("duration_ms"),
            "success_rate": metrics.get("outcomes", {}).get("success_rate"),
            "confirmed_outcomes": metrics.get("outcomes", {}).get("confirmed", 0),
            "unknown_outcomes": metrics.get("outcomes", {}).get("unknown", 0),
            "review_coverage": metrics.get("outcomes", {}).get("review_coverage"),
            "observation_coverage": (
                metrics.get("idle", {}).get("coverage") or {}).get("usable_ratio"),
            "detection_f1": metrics.get("detection", {}).get("f1"),
            "detection_quality_available": metrics.get("detection", {}).get("available", False),
        })
    mismatch = [key for key, values in fingerprints.items() if len(values) > 1]
    if mismatch:
        raise ValueError(f"measurement instrument differs across reports: {', '.join(mismatch)}")
    if missing:
        provisional_reasons.add("instrument_provenance_incomplete:" + ",".join(sorted(missing)))
    rows.sort(key=lambda row: (row["experiment_id"], row["controller_label"] or "",
                               row["policy_label"] or "", row["report_hash"]))
    result = {
        "schema_version": 2,
        "kind": "camera_measurement_experiment_comparison",
        "protocol_id": protocol_id,
        "protocol_hash": protocol_hash,
        "status": "provisional" if provisional_reasons else "qualified",
        "provisional_reasons": sorted(provisional_reasons),
        "instrument": {key: json.loads(next(iter(values))) if values else None
                       for key, values in fingerprints.items()},
        "experiments": rows,
        "limitations": [
            "Controller and policy labels are supplied metadata, not inferred identities.",
            "Camera measurement does not train, execute or control the robot.",
            "Different task conditions can confound comparisons even under the same protocol.",
            "No automatic policy winner or physical qualification is inferred from measurements.",
        ],
    }
    result["comparison_hash"] = hashlib.sha256(json.dumps(
        result, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
    return result


def write_comparison(out_dir: Path, comparison: dict) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = comparison["experiments"]
    columns = ["experiment_id", "controller_label", "policy_label", "session_id", "run_id",
               "report_hash", "cycle_count", "mean_cycle_ms", "p95_cycle_ms",
               "throughput_cycles_per_min", "session_duration_ms", "success_rate",
               "confirmed_outcomes", "unknown_outcomes", "review_coverage", "observation_coverage",
               "detection_f1", "detection_quality_available"]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_safe(row.get(key)) for key in columns])
    markdown = [
        "# Signoff experiment comparison", "",
        f"Status: **{comparison['status']}**",
        f"Protocol: {comparison['protocol_id']} ({comparison['protocol_hash']})",
        f"Comparison hash: {comparison['comparison_hash']}", "",
        "| Experiment | Controller/policy | Cycles | Mean cycle (ms) | Throughput/min |",
        "|---|---|---:|---:|---:|",
    ]
    for row in rows:
        label = row["policy_label"] or row["controller_label"]
        values = [row["experiment_id"], label, row["cycle_count"], row["mean_cycle_ms"],
                  row["throughput_cycles_per_min"]]
        markdown.append("| " + " | ".join(str(value).replace("|", "\\|").replace(
            "\n", " ").replace("<", "&lt;") if value is not None else "Unavailable"
            for value in values) + " |")
    markdown += ["", "Limitations:"] + ["- " + item for item in comparison["limitations"]]
    markdown += ["- " + item for item in comparison["provisional_reasons"]]
    outputs = {
        "csv": ("comparison.csv", stream.getvalue()),
        "markdown": ("comparison.md", "\n".join(markdown) + "\n"),
        "json": ("comparison.json", json.dumps(comparison, indent=2, sort_keys=True,
                                               allow_nan=False)),
    }
    for filename, content in outputs.values():
        path = out_dir / filename
        if path.exists() and path.read_bytes() != content.encode("utf-8"):
            raise ValueError("comparison artifacts are immutable")
    for filename, content in outputs.values():
        _publish_immutable(out_dir / filename, content)
    return {name: out_dir / filename for name, (filename, _) in outputs.items()}
