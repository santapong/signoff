"""Run frozen physical-video qualification without exposing references to inference.

Usage: python tools/qualify_dataset.py manifest.json --data-root DATA --out REPORT.json
Exit 0 means the physical measurement gates passed; 2 means pending/not qualified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("dataset paths must be relative to the explicit data root")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("dataset file is missing or escapes the data root")
    return path


def evaluate_dataset(manifest_path: Path, data_root: Path | None = None) -> dict:
    from packages.cv.signoff_cv.pipeline import analyze_video

    from packages.cv.signoff_cv.evaluate import evaluate_cycles
    from packages.domain.profiles import validate_identifier, validate_profile

    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    root = Path(data_root) if data_root is not None else manifest_path.parent
    dataset_id = validate_identifier(manifest.get("dataset_id", ""))
    if manifest.get("schema_version") != 2:
        raise ValueError("qualification manifest schema_version must be 2")
    source_kind = manifest.get("source_kind", "unknown")
    allowed = {"physical_robot_station", "manual_tabletop_rehearsal", "simulation",
               "public_robot_video", "unknown"}
    if source_kind not in allowed:
        raise ValueError("unknown source_kind")
    reasons = []
    if source_kind != "physical_robot_station":
        reasons.append("source is not an owner-attested physical robot station")
    if not manifest.get("frozen_at"):
        reasons.append("session split was not frozen before evaluation")
    if not manifest.get("capture_attested_by"):
        reasons.append("physical capture attestation is missing")
    annotators = manifest.get("annotators", [])
    if len(set(annotators)) < 2:
        reasons.append("two independent annotation identities are required")
    rows = manifest.get("sessions", [])
    if not isinstance(rows, list):
        raise ValueError("sessions must be an array")
    seen_ids = set()
    source_splits: dict[str, str] = {}
    validated = []
    # Validate all frozen input hashes before reading labels or running inference.
    for row in rows:
        sid = validate_identifier(row["session_id"])
        split = row.get("split")
        if sid in seen_ids or split not in ("development", "validation", "test", "ood"):
            raise ValueError("duplicate session identity or invalid split")
        seen_ids.add(sid)
        inputs = {}
        for key in ("video", "reference", "profile"):
            path = _path(root, row[key])
            expected = row.get(key + "_sha256")
            if not expected or _sha(path) != expected:
                raise ValueError(f"{sid}: {key} hash is absent or changed")
            inputs[key] = path
        video_hash = row["video_sha256"]
        if video_hash in source_splits and source_splits[video_hash] != split:
            raise ValueError("the same video cannot cross frozen session splits")
        source_splits[video_hash] = split
        profile = validate_profile(json.loads(inputs["profile"].read_text()))
        if row.get("profile_hash") != profile["profile_hash"]:
            raise ValueError(f"{sid}: profile hash changed")
        validated.append((row, inputs, profile))
    scored = []
    for row, inputs, profile in validated:
        if row["split"] not in ("test", "ood"):
            continue
        # Reference files are intentionally never an argument to analyze_video.
        result = analyze_video(inputs["video"], profile=profile)
        reference = [json.loads(line) for line in inputs["reference"].read_text().splitlines()
                     if line.strip()]
        score = evaluate_cycles(reference, result["detected_cycles"],
                                reference_id=row["session_id"],
                                reference_revision=row["reference_sha256"])
        cov = result.get("coverage", {})
        provenance = result.get("provenance", {})
        ratio = cov.get("usable_ratio")
        fps = provenance.get("observed_fps")
        timing = score.get("timing_errors", {})
        timing_pass = all(
            timing.get(name, {}).get("p95_absolute_ms") is not None
            and timing[name]["p95_absolute_ms"] <= limit
            for name, limit in (("start", 250), ("end", 250), ("duration", 500)))
        metric_pass = all(score.get(k) is not None and score[k] >= .95
                          for k in ("precision", "recall", "f1"))
        passed = (metric_pass and timing_pass
                  and score.get("count_error", {}).get("absolute_percent", 100) <= 2
                  and ratio is not None and ratio >= .95
                  and fps is not None and fps >= 10
                  and profile["detector"]["method"] == "aruco")
        scored.append({"session_id": row["session_id"], "split": row["split"],
                       "score": score, "coverage": cov, "provenance": provenance,
                       "analysis_hash": result.get("analysis_hash"), "passed": passed})
    nominal = [r for r in scored if r["split"] == "test"]
    ood = [r for r in scored if r["split"] == "ood"]
    test_cycles = sum(r["score"]["count_error"]["reference"] for r in nominal)
    ood_cycles = sum(r["score"]["count_error"]["reference"] for r in ood)
    if len(nominal) < 2 or test_cycles < 120:
        reasons.append("held-out nominal test requires two sessions and at least 120 cycles")
    if ood_cycles < 100:
        reasons.append("changed-condition assessment requires at least 100 cycles")
    if not nominal or not all(r["passed"] for r in nominal):
        reasons.append("one or more nominal measurement gates did not pass")
    qualifying = not reasons
    return {"schema_version": 2, "dataset_id": dataset_id, "source_kind": source_kind,
            "manifest_sha256": _sha(manifest_path), "qualification": {
                "status": "qualified" if qualifying else "pending",
                "physically_qualified": qualifying, "reasons": reasons,
                "nominal_reference_cycles": test_cycles, "ood_reference_cycles": ood_cycles,
                "unsupported_conditions": [r["session_id"] for r in ood if not r["passed"]],
                "annotators": annotators,
                "annotator_kinds": manifest.get("annotator_kinds", {}),
                "capture_attested_by": manifest.get("capture_attested_by")},
            "sessions": scored}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = evaluate_dataset(args.manifest, args.data_root)
    except (ValueError, KeyError, OSError) as exc:
        print(f"qualification rejected: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    print(report["qualification"]["status"], args.out)
    return 0 if report["qualification"]["physically_qualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
