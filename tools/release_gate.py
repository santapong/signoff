"""Read-only production eligibility gate; absent physical receipts remain PENDING.

This command never tags, deploys or publishes. An OWNER acceptance is a local
owner pilot, including assistance where disclosed, and is not external validation.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.metrics.signoff_metrics import cycle_durations, throughput  # noqa: E402
from packages.reporting.signoff_report import verify_report  # noqa: E402
from tools.qualify_dataset import (  # noqa: E402
    MINIMA,
    _digest,
    _sha,
    measurement_digest,
    qualification_receipt_hash,
    score_passes,
    validate_frozen_inputs,
    validate_soak_receipt,
)


def _load(path: Path | None, kind: str, reasons: list[str]) -> dict | None:
    if path is None or not Path(path).is_file():
        reasons.append(f"{kind} receipt/file is missing")
        return None
    try:
        value = json.loads(Path(path).read_text())
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except (ValueError, OSError) as exc:
        reasons.append(f"{kind} receipt/file is invalid: {exc}")
        return None


def _check_physical(qualification: dict, context: dict, reasons: list[str]) -> list[dict]:
    outcome = qualification.get("qualification", {})
    digest = qualification_receipt_hash(qualification)
    if (
        qualification.get("schema_version") != 2
        or qualification.get("kind") != "physical_camera_qualification"
        or qualification.get("receipt_hash") != digest
        or outcome.get("receipt_hash") != digest
    ):
        reasons.append("physical qualification receipt integrity/schema is invalid")
    if (
        qualification.get("source_kind") != "physical_robot_station"
        or outcome.get("physical") is not True
        or outcome.get("physically_qualified") is not True
        or outcome.get("status") != "qualified"
        or outcome.get("reasons")
    ):
        reasons.append("actual physical qualification has not passed")
    if qualification.get("manifest_sha256") != context["manifest_sha256"]:
        reasons.append("qualification does not bind the selected frozen manifest")
    if qualification.get("split_summary") != context["split_summary"]:
        reasons.append("qualification denominators differ from current frozen artifacts")
    scope = qualification.get("scope", {})
    for key in ("protocol_id", "protocol_hash"):
        if not context[key] or scope.get(key) != context[key]:
            reasons.append(f"qualification {key} does not bind the frozen protocol")
    inactivity = qualification.get("inactivity", {})
    idle_frozen = {key: value for key, value in inactivity.items() if key != "review_hash"}
    if (
        inactivity.get("available") is not True
        or inactivity.get("reference_episodes", 0) < 100
        or inactivity.get("reference_episodes") != context.get("inactivity_episodes")
        or inactivity.get("review_hash") != _digest(idle_frozen)
    ):
        reasons.append("independently annotated inactivity scoring evidence is missing or changed")
    idle_current = {
        item["row"]["session_id"]: item
        for item in context["validated"]
        if item.get("inactivity_reference")
    }
    idle_scored = inactivity.get("sessions", [])
    if {item.get("session_id") for item in idle_scored} != set(idle_current):
        reasons.append("inactivity receipt sessions differ from frozen held-out evidence")
    for item in idle_scored:
        selected = idle_current.get(item.get("session_id"))
        if selected is None:
            continue
        row = selected["row"]
        score = item.get("score", {})
        if (
            item.get("source_sha256") != row["video_sha256"]
            or item.get("reference_sha256") != row["inactivity_reference_sha256"]
            or item.get("annotation_artifacts") != selected["inactivity_artifacts"]
            or item.get("adjudication") != row["inactivity_adjudication"]
            or score.get("available") is not True
            or score.get("reference_revision") != row["inactivity_reference_sha256"]
            or score.get("matcher") != "two_boundary_max_cardinality_min_error"
            or score.get("count_error", {}).get("reference")
            != len(selected["inactivity_reference"])
            or "timing_errors" not in score
            or "coverage" not in item
        ):
            reasons.append("inactivity scoring source/clock/reference lineage differs")
    if qualification.get("soak") != context.get("soak"):
        reasons.append("live soak receipt differs from frozen physical evidence")
    reasons.extend(
        validate_soak_receipt(qualification.get("soak"), scope, context.get("soak_sources"))
    )
    for split, (sessions, cycles) in MINIMA.items():
        summary = qualification.get("split_summary", {}).get(split, {})
        if summary.get("sessions", 0) < sessions or summary.get("reference_cycles", 0) < cycles:
            reasons.append(f"physical qualification lacks required {split} denominator")
    current = {
        item["row"]["session_id"]: item
        for item in context["validated"]
        if item["row"]["split"] in ("test", "ood")
    }
    scored = qualification.get("sessions", [])
    if not isinstance(scored, list):
        reasons.append("qualification session evidence is missing")
        return []
    ids = [item.get("session_id") for item in scored]
    if len(set(ids)) != len(ids) or set(ids) != set(current):
        reasons.append("qualification session identities differ from frozen held-out sessions")
    nominal = []
    for item in scored:
        source = current.get(item.get("session_id"))
        if source is None:
            continue
        row = source["row"]
        expected_hashes = {
            key: row[key] for key in ("video_sha256", "reference_sha256", "profile_sha256")
        }
        if (
            item.get("input_hashes") != expected_hashes
            or item.get("sources") != source["sources"]
            or item.get("annotation_artifacts") != source["annotation_artifacts"]
            or item.get("adjudication") != row.get("adjudication")
            or item.get("split") != row["split"]
        ):
            reasons.append(f"{row['session_id']}: qualified source/reference lineage differs")
        score = item.get("score", {})
        if (
            score.get("reference_revision") != row["reference_sha256"]
            or score.get("reference_id") != row["session_id"]
            or score.get("count_error", {}).get("reference") != len(source["reference"])
        ):
            reasons.append(f"{row['session_id']}: qualified reference denominator/identity differs")
        if row["split"] != "test":
            continue
        nominal.append(item)
        if item.get("passed") is not True or not score_passes(
            score, item.get("coverage", {}), item.get("provenance", {})
        ):
            reasons.append(f"{row['session_id']}: nominal score gates are not satisfied")
        for key in ("profile_hash", "configuration_hash", "code_sha"):
            if not scope.get(key) or item.get("provenance", {}).get(key) != scope[key]:
                reasons.append(f"{row['session_id']}: measurement scope {key} differs")
    return nominal


def evaluate_release(
    *,
    qualification_path: Path | None = None,
    pilot_path: Path | None = None,
    report_path: Path | None = None,
    manifest_path: Path | None = None,
    data_root: Path | None = None,
) -> dict:
    reasons: list[str] = []
    qualification = _load(qualification_path, "qualification", reasons)
    pilot = _load(pilot_path, "owner pilot", reasons)
    report = _load(report_path, "measurement report", reasons)
    context = None
    if manifest_path is None or not Path(manifest_path).is_file():
        reasons.append("frozen qualification manifest is missing")
    else:
        try:
            context = validate_frozen_inputs(manifest_path, data_root)
            context["manifest_sha256"] = _sha(Path(manifest_path))
            reasons += context["reasons"]
        except (ValueError, TypeError, KeyError, OSError) as exc:
            reasons.append(f"current frozen inputs are invalid: {exc}")
    hashes = {}
    for label, path in (
        ("qualification_sha256", qualification_path),
        ("report_sha256", report_path),
        ("manifest_sha256", manifest_path),
    ):
        if path is not None and Path(path).is_file():
            hashes[label] = _sha(Path(path))
    nominal = []
    if qualification is not None and context is not None:
        nominal = _check_physical(qualification, context, reasons)
    intact = False
    if report is not None:
        try:
            intact = verify_report(report)
        except (TypeError, ValueError):
            intact = False
        if not intact:
            reasons.append("measurement report is not an intact schema-v2 snapshot")
    if intact and qualification is not None:
        provenance, scope = report.get("provenance", {}), qualification.get("scope", {})
        for key in ("profile_hash", "configuration_hash", "code_sha"):
            if not scope.get(key) or provenance.get(key) != scope[key]:
                reasons.append(f"measurement report {key} differs from qualified scope")
        for key in ("protocol_id", "protocol_hash"):
            # Older measurement snapshots acquire protocol binding through the
            # accepted qualification receipt and exact qualified source, not a rewrite.
            if key in provenance and provenance[key] != scope.get(key):
                reasons.append(f"measurement report {key} differs from qualified protocol")
        source_hash = provenance.get("source_sha256", provenance.get("video_sha256"))
        if (
            provenance.get("source_sha256")
            and provenance.get("video_sha256")
            and provenance["source_sha256"] != provenance["video_sha256"]
        ):
            reasons.append("measurement report source hashes disagree")
        matches = [
            item
            for item in nominal
            if item.get("input_hashes", {}).get("video_sha256") == source_hash
        ]
        if len(matches) != 1:
            reasons.append("report source is not one qualified nominal session")
        else:
            selected = matches[0]
            if report.get("session", {}).get("session_id") != selected["session_id"]:
                reasons.append("report session identity differs from qualified source")
            if measurement_digest(report.get("detected_cycles", [])) != selected.get(
                "detected_cycles_hash"
            ):
                reasons.append("report observations differ from qualified camera observations")
            count = report.get("metrics", {}).get("cycle_durations", {}).get("n")
            if count != selected.get("score", {}).get("count_error", {}).get("detected"):
                reasons.append("report count differs from qualified camera observations")
            duration = report.get("metrics", {}).get("throughput", {}).get("duration_ms")
            if (
                not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration != selected.get("video_duration_ms")
            ):
                reasons.append("report full-session duration differs from qualified observations")
            else:
                try:
                    expected_durations = cycle_durations(report.get("detected_cycles", []))
                    if any(
                        report.get("metrics", {}).get("cycle_durations", {}).get(key) != value
                        for key, value in expected_durations.items()
                    ):
                        reasons.append("report cycle timing metrics differ from observations")
                    expected_rate = throughput(report.get("detected_cycles", []), duration)
                    if (
                        report.get("metrics", {}).get("throughput", {}).get("cycles_per_min")
                        != expected_rate["cycles_per_min"]
                    ):
                        reasons.append(
                            "report throughput differs from the full-session denominator"
                        )
                except (ValueError, TypeError, KeyError):
                    reasons.append("report observation boundaries are invalid")
    owner_accepted = False
    if pilot is not None:
        if (
            pilot.get("schema_version") != 2
            or pilot.get("pilot_type") != "owner"
            or pilot.get("reviewer_kind") != "OWNER"
            or not str(pilot.get("reviewer") or "").strip()
            or pilot.get("decision") != "ACCEPT"
            or not str(pilot.get("reason") or "").strip()
        ):
            reasons.append("a named OWNER ACCEPT decision with reason is required")
        else:
            owner_accepted = True
        for key in ("report_sha256", "qualification_sha256", "manifest_sha256"):
            if not hashes.get(key) or pilot.get(key) != hashes[key]:
                reasons.append(f"owner acceptance does not bind {key}")
        if report is None or pilot.get("report_hash") != report.get("report_hash"):
            reasons.append("owner acceptance does not bind the internal report_hash")
        if report is None or pilot.get("run_id") != report.get("run_id") or not pilot.get("run_id"):
            reasons.append("owner acceptance does not bind the selected measurement run")
        inactivity = (qualification or {}).get("inactivity", {})
        if (
            pilot.get("inactivity_acknowledged") is not True
            or not inactivity.get("review_hash")
            or pilot.get("inactivity_review_hash") != inactivity["review_hash"]
        ):
            reasons.append(
                "owner acceptance does not acknowledge the bound inactivity scoring evidence"
            )
    ready = not reasons
    return {
        "schema_version": 2,
        "kind": "production_release_gate",
        "status": "READY" if ready else "PENDING",
        "release_channel": "production_eligible" if ready else "release_candidate",
        "physical_ready": ready,
        "owner_accepted": owner_accepted,
        "pilot_kind": "OWNER"
        if pilot and pilot.get("reviewer_kind") == "OWNER" and pilot.get("pilot_type") == "owner"
        else None,
        "external_validation": False,
        "blockers": sorted(set(reasons)),
        "evidence_hashes": hashes,
        "report_hash": report.get("report_hash") if report else None,
        "qualification_receipt_hash": qualification.get("receipt_hash") if qualification else None,
        "protocol_binding": "qualification_manifest_and_exact_report_source" if ready else None,
        "action": "read_only_eligibility_check",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("qualification", "pilot", "report", "manifest", "data-root", "out"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    try:
        result = evaluate_release(
            qualification_path=args.qualification,
            pilot_path=args.pilot,
            report_path=args.report,
            manifest_path=args.manifest,
            data_root=args.data_root,
        )
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        result = {
            "schema_version": 2,
            "status": "PENDING",
            "physical_ready": False,
            "release_channel": "release_candidate",
            "blockers": [str(exc)],
        }
    text = json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    return 0 if result["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
