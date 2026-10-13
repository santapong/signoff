"""Score a frozen camera dataset; exit 2 preserves pending physical qualification.

Inputs are owner-attested capture metadata plus hashed media, clocks, profiles,
independent annotation artifacts and an explicitly adjudicated reference.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MINIMA = {"development": (6, 360), "validation": (2, 120), "test": (2, 120), "ood": (1, 100)}
MATCHER = "two_boundary_max_cardinality_min_error"
CONTRACT = "signoff-camera-v0.2"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def _path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("dataset paths must be relative to the explicit data root")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("dataset file is missing or escapes the data root")
    return path


def _hashed(root: Path, row: dict, key: str) -> Path:
    path = _path(root, row[key])
    expected = row.get(key + "_sha256")
    if not isinstance(expected, str) or len(expected) != 64 or _sha(path) != expected:
        raise ValueError(f"{key} hash is absent or changed")
    return path


def _labels(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("annotations must contain objects")
    if any(row.get("cycle_id", row.get("cycle")) is None for row in rows):
        raise ValueError("annotation cycle identities must be explicit")
    # Validate identities/boundaries without running inference or consulting predictions.
    from packages.cv.signoff_cv.evaluate import evaluate_cycles

    evaluate_cycles(rows, [], reference_id=_sha(path))
    return rows


def _cycle_key(row: dict) -> str:
    return str(row.get("cycle_id", row.get("cycle")))


def _bound(row: dict, boundary: str) -> float:
    return float(row.get(boundary + "_ms", row.get("cycle_" + boundary + "_ms")))


def _media_inputs(root: Path, row: dict, video: Path) -> tuple[list[dict], Path | None]:
    """Hash every media/clock input that the decoder can actually consume."""
    if video.name.endswith(".manifest.json"):
        recording = json.loads(video.read_text())
        if recording.get("status") != "complete" or not recording.get("segments"):
            raise ValueError("qualification recording must contain finalized complete segments")
        sources = []
        for segment in recording["segments"]:
            item = {}
            for key, hash_key in (("file", "sha256"), ("clock_file", "clock_sha256")):
                name = segment.get(key)
                if not isinstance(name, str) or Path(name).name != name:
                    raise ValueError("recording segment paths must be plain filenames")
                path = _path(root, str((video.parent / name).relative_to(root.resolve())))
                if _sha(path) != segment.get(hash_key):
                    raise ValueError("recording segment or clock hash changed")
                item[key] = str(path.relative_to(root.resolve()))
                item[hash_key] = _sha(path)
            sources.append(item)
        return sources, None
    discovered = next(
        (
            path
            for path in (video.with_suffix(".clock.jsonl"), video.parent / "frame_index.jsonl")
            if path.exists()
        ),
        None,
    )
    clock = _hashed(root, row, "clock") if row.get("clock") is not None else None
    if discovered is not None and clock is None:
        raise ValueError("auto-loaded clock must be explicitly frozen with clock_sha256")
    if clock is not None and discovered is not None and clock != discovered.resolve():
        raise ValueError("frozen clock differs from the clock selected by the decoder")
    return [
        {
            "file": str(video.relative_to(root.resolve())),
            "sha256": _sha(video),
            "clock_file": str(clock.relative_to(root.resolve())) if clock else None,
            "clock_sha256": _sha(clock) if clock else None,
        }
    ], clock


def _annotation_lineage(
    root: Path, row: dict, reference: list[dict], annotators: list[str], kinds: dict
) -> tuple[list[dict], list[dict]]:
    descriptors = row.get("annotations")
    if not isinstance(descriptors, list) or len(descriptors) < 2:
        raise ValueError("held-out session requires preserved independent annotation artifacts")
    artifacts, by_annotator, seen_hashes = [], {}, set()
    reference_ids = {_cycle_key(item) for item in reference}
    for descriptor in descriptors:
        name = descriptor.get("annotator")
        if (
            name not in annotators
            or name in by_annotator
            or kinds.get(name) not in ("human", "ai_assisted")
        ):
            raise ValueError("annotation identity/kind is missing, duplicated or undeclared")
        if descriptor.get("independently_labeled") is not True:
            raise ValueError("annotation independence requires an explicit attestation")
        path = _hashed(
            root,
            {"artifact": descriptor["file"], "artifact_sha256": descriptor.get("sha256")},
            "artifact",
        )
        digest = _sha(path)
        if digest in seen_hashes:
            raise ValueError("independent annotation artifacts cannot be duplicate bytes")
        seen_hashes.add(digest)
        labels = _labels(path)
        if any(
            label.get("annotator") != name or label.get("session") != row["session_id"]
            for label in labels
        ):
            raise ValueError("annotation rows must identify the declared reviewer and session")
        by_id = {_cycle_key(item): item for item in labels}
        if set(by_id) != reference_ids:
            raise ValueError("adjudicated reference must link the same cycles from each annotation")
        by_annotator[name] = by_id
        artifacts.append(
            {
                "annotator": name,
                "kind": kinds[name],
                "file": descriptor["file"],
                "sha256": digest,
                "independently_labeled": True,
            }
        )
    if not any(kinds[name] == "human" for name in by_annotator):
        raise ValueError("annotation validation requires a named human reviewer")
    adjudication = row.get("adjudication", {})
    if (
        adjudication.get("reference_sha256") != row["reference_sha256"]
        or set(adjudication.get("annotation_sha256", [])) != seen_hashes
        or not str(adjudication.get("reason", "")).strip()
        or kinds.get(adjudication.get("adjudicator")) != "human"
    ):
        raise ValueError(
            "adjudication must link reference/all annotation hashes and a human reason"
        )
    agreements = []
    for first, second in combinations(sorted(by_annotator), 2):
        first_rows, second_rows = by_annotator[first], by_annotator[second]
        keys = sorted(reference_ids)
        agreements.append(
            {
                "annotators": [first, second],
                "kinds": [kinds[first], kinds[second]],
                "common_cycles": len(keys),
                "outcome_disagreements": sum(
                    first_rows[key].get("outcome", "UNKNOWN")
                    != second_rows[key].get("outcome", "UNKNOWN")
                    for key in keys
                ),
                "boundary_disagreements": sum(
                    any(
                        _bound(first_rows[key], boundary) != _bound(second_rows[key], boundary)
                        for boundary in ("start", "end")
                    )
                    for key in keys
                ),
            }
        )
    return artifacts, agreements


def validate_frozen_inputs(manifest_path: Path, data_root: Path | None = None) -> dict:
    """Read-only validation also used by release gating; never invokes inference."""
    from packages.domain.profiles import validate_identifier, validate_profile

    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    root = (Path(data_root) if data_root is not None else manifest_path.parent).resolve()
    if manifest.get("schema_version") != 2:
        raise ValueError("qualification manifest schema_version must be 2")
    dataset_id = validate_identifier(manifest.get("dataset_id", ""))
    source_kind = manifest.get("source_kind", "unknown")
    if source_kind not in {
        "physical_robot_station",
        "manual_tabletop_rehearsal",
        "simulation",
        "public_robot_video",
        "unknown",
    }:
        raise ValueError("unknown source_kind")
    reasons = []
    if source_kind != "physical_robot_station":
        reasons.append("source is not an owner-attested physical robot station")
    if not manifest.get("frozen_at"):
        reasons.append("session split was not frozen before evaluation")
    if not str(manifest.get("capture_attested_by") or "").strip():
        reasons.append("physical capture attestation is missing")
    annotators, kinds = manifest.get("annotators", []), manifest.get("annotator_kinds", {})
    if (
        not isinstance(annotators, list)
        or not all(isinstance(name, str) and name.strip() for name in annotators)
        or len(set(annotators)) != len(annotators)
    ):
        raise ValueError("annotators must be distinct nonempty identities")
    if not isinstance(kinds, dict) or any(
        kinds.get(name) not in ("human", "ai_assisted") for name in annotators
    ):
        reasons.append("every reviewer must declare human or ai_assisted annotation kind")
    if len(annotators) < 2:
        reasons.append("two independently preserved annotation identities are required")
    protocol = None
    protocol_hash = None
    protocol_id = manifest.get("protocol_id")
    if manifest.get("protocol"):
        protocol = _hashed(root, manifest, "protocol")
        contract = json.loads(protocol.read_text())
        if contract.get("protocol_id") != protocol_id or contract.get("contract") != CONTRACT:
            raise ValueError("frozen protocol identity/measurement contract mismatch")
        protocol_hash = _sha(protocol)
    else:
        reasons.append("frozen protocol artifact is missing")
    rows = manifest.get("sessions", [])
    if not isinstance(rows, list):
        raise ValueError("sessions must be an array")
    seen_ids, seen_media, seen_videos = set(), set(), set()
    validated = []
    counts: Counter = Counter()
    cycles: Counter = Counter()
    nominal_profiles = set()
    for row in rows:
        sid = validate_identifier(row["session_id"])
        split = row.get("split")
        if sid in seen_ids or split not in MINIMA:
            raise ValueError("duplicate session identity or invalid split")
        seen_ids.add(sid)
        inputs = {key: _hashed(root, row, key) for key in ("video", "reference", "profile")}
        if row["video_sha256"] in seen_videos:
            raise ValueError("duplicate footage cannot inflate session or cycle denominators")
        seen_videos.add(row["video_sha256"])
        sources, clock = _media_inputs(root, row, inputs["video"])
        for item in sources:
            if item["sha256"] in seen_media:
                raise ValueError("duplicate media segments cannot enter more than one session")
            seen_media.add(item["sha256"])
        inputs["clock"] = clock
        profile = validate_profile(json.loads(inputs["profile"].read_text()))
        if row.get("profile_hash") != profile["profile_hash"]:
            raise ValueError(f"{sid}: profile hash changed")
        reference = _labels(inputs["reference"])
        if split != "ood":
            nominal_profiles.add(profile["profile_hash"])
            if len(reference) < 60:
                reasons.append(f"{sid}: nominal session requires at least 60 reference cycles")
        counts[split] += 1
        cycles[split] += len(reference)
        artifacts, agreements = [], []
        if split in ("test", "ood"):
            artifacts, agreements = _annotation_lineage(root, row, reference, annotators, kinds)
        inactivity, inactivity_artifacts, inactivity_agreement = [], [], []
        if split in ("test", "ood") and row.get("inactivity_reference"):
            path = _hashed(root, row, "inactivity_reference")
            inactivity = _labels(path)
            idle_row = {
                **row,
                "reference_sha256": row["inactivity_reference_sha256"],
                "annotations": row.get("inactivity_annotations"),
                "adjudication": row.get("inactivity_adjudication", {}),
            }
            inactivity_artifacts, inactivity_agreement = _annotation_lineage(
                root, idle_row, inactivity, annotators, kinds
            )
        validated.append(
            {
                "row": row,
                "inputs": inputs,
                "profile": profile,
                "reference": reference,
                "sources": sources,
                "annotation_artifacts": artifacts,
                "annotation_agreement": agreements,
                "inactivity_reference": inactivity,
                "inactivity_artifacts": inactivity_artifacts,
                "inactivity_agreement": inactivity_agreement,
            }
        )
    if len(nominal_profiles) != 1:
        reasons.append("nominal qualification must use exactly one frozen station profile")
    for split, (minimum_sessions, minimum_cycles) in MINIMA.items():
        if counts[split] < minimum_sessions or cycles[split] < minimum_cycles:
            reasons.append(
                f"{split} requires at least {minimum_sessions} distinct sessions and "
                f"{minimum_cycles} reference cycles"
            )
    inactivity_episodes = sum(len(item["inactivity_reference"]) for item in validated)
    if inactivity_episodes < 100:
        reasons.append(
            "inactivity assessment requires 100 independently annotated held-out episodes"
        )
    soak, soak_sources = None, []
    if manifest.get("soak") and manifest.get("soak_media"):
        soak = json.loads(_hashed(root, manifest, "soak").read_text())
        media = _hashed(root, manifest, "soak_media")
        soak_sources, _ = _media_inputs(root, {}, media)
        if soak.get("recording", {}).get("manifest_sha256") != _sha(media):
            raise ValueError("live soak does not identify its frozen recording manifest")
        recording = json.loads(media.read_text())
        duration = recording.get("end_ms")
        if not isinstance(duration, (int, float)) or duration != soak.get("duration_ms"):
            raise ValueError("live soak duration differs from the original recording clock")
        observations = sum(
            sum(
                1
                for line in _path(root, item["clock_file"]).read_text().splitlines()
                if line.strip()
            )
            for item in soak_sources
        )
        if observations != soak.get("observation_count"):
            raise ValueError("live soak observation count differs from archived frame clocks")
    else:
        reasons.append("actual live camera soak receipt and original recording are missing")
    return {
        "manifest": manifest,
        "dataset_id": dataset_id,
        "root": root,
        "protocol_id": protocol_id,
        "protocol_hash": protocol_hash,
        "reasons": reasons,
        "validated": validated,
        "soak": soak,
        "soak_sources": soak_sources,
        "inactivity_episodes": inactivity_episodes,
        "split_summary": {
            split: {"sessions": counts[split], "reference_cycles": cycles[split]}
            for split in MINIMA
        },
    }


def measurement_digest(cycles: list[dict]) -> str:
    keys = ("cycle_id", "start_ms", "end_ms", "status", "start_frame", "end_frame", "basis")
    return _digest([{key: cycle.get(key) for key in keys} for cycle in cycles])


def qualification_receipt_hash(receipt: dict) -> str:
    snapshot = copy.deepcopy(receipt)
    snapshot.pop("receipt_hash", None)
    snapshot.get("qualification", {}).pop("receipt_hash", None)
    return _digest(snapshot)


def _finite_at_least(value, minimum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= minimum
    )


def validate_soak_receipt(
    receipt: dict | None, scope: dict, sources: list[dict] | None = None
) -> list[str]:
    """Validate observed soak evidence using the already-published latency targets."""
    if not receipt:
        return ["actual live camera soak receipt is missing"]
    reasons = []
    frozen = {key: value for key, value in receipt.items() if key != "receipt_hash"}
    if (
        receipt.get("schema_version") != 3
        or receipt.get("kind") != "live_camera_soak"
        or receipt.get("receipt_hash") != _digest(frozen)
    ):
        reasons.append("live soak receipt integrity/schema is invalid")
    if (
        receipt.get("source_kind") != "physical_robot_station"
        or receipt.get("real_observations") is not True
        or not str(receipt.get("capture_attested_by") or "").strip()
    ):
        reasons.append("live soak lacks owner-attested actual physical station observations")
    if not _finite_at_least(receipt.get("duration_ms"), 7200000):
        reasons.append("live soak must contain two hours of actual recording observations")
    for key in ("profile_hash", "configuration_hash", "code_sha"):
        if not scope.get(key) or receipt.get("scope", {}).get(key) != scope[key]:
            reasons.append(f"live soak {key} differs from the qualified measurement scope")
    coverage, latency = receipt.get("coverage", {}), receipt.get("latency", {})
    if (
        not _finite_at_least(coverage.get("usable_ratio"), 0.95)
        or coverage.get("usable_ratio", 2) > 1
        or not _finite_at_least(coverage.get("usable_fps"), 10)
    ):
        reasons.append("live soak usable coverage/rate do not meet the published targets")
    count = receipt.get("observation_count")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count <= 0
        or latency.get("sample_count") != count
        or not _finite_at_least(latency.get("qualified_event_count"), 1)
        or latency.get("clock_basis") != "host_ingest_to_publication"
        or not _finite_at_least(latency.get("p95_ms"), 0)
        or latency["p95_ms"] > 1000
        or not _finite_at_least(latency.get("max_ms"), 0)
        or latency["max_ms"] > 5000
    ):
        reasons.append("live soak latency samples/basis do not meet the published targets")
    if receipt.get("recording", {}).get("integrity_verified") is not True:
        reasons.append("live soak recording integrity was not verified")
    recorded = receipt.get("source_hashes", [])
    expected = sources or []

    def identities(rows):
        return sorted((row.get("sha256"), row.get("clock_sha256")) for row in rows)

    if not recorded or not expected or identities(recorded) != identities(expected):
        reasons.append("live soak original media/clock hashes differ from frozen evidence")
    return reasons


def score_passes(score: dict, coverage: dict, provenance: dict) -> bool:
    timing = score.get("timing_errors", {})
    clock_bases = provenance.get("timestamp_basis", [])
    counts = [score.get(key) for key in ("true_positives", "false_positives", "false_negatives")]
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counts
    ):
        return False
    true, false_positive, false_negative = counts
    references, detections = true + false_negative, true + false_positive
    if (
        true < 1
        or score.get("count_error", {}).get("reference") != references
        or score.get("count_error", {}).get("detected") != detections
    ):
        return False
    expected = {
        "precision": round(true / detections, 4),
        "recall": round(true / references, 4),
        "f1": round(2 * true / (2 * true + false_positive + false_negative), 4),
    }
    if any(score.get(key) != value for key, value in expected.items()):
        return False
    return (
        score.get("available") is True
        and score.get("matcher") == MATCHER
        and score.get("tolerance_ms") == 500
        and bool(score.get("reference_id"))
        and all(_finite_at_least(score.get(key), 0.95) for key in ("precision", "recall", "f1"))
        and all(
            isinstance(timing.get(name, {}).get("p95_absolute_ms"), (int, float))
            and 0 <= timing[name]["p95_absolute_ms"] <= limit
            and timing[name].get("n", 0) == score.get("true_positives")
            for name, limit in (("start", 250), ("end", 250), ("duration", 500))
        )
        and _finite_at_least(score.get("count_error", {}).get("absolute_percent"), 0)
        and score["count_error"]["absolute_percent"] <= 2
        and _finite_at_least(coverage.get("usable_ratio"), 0.95)
        and coverage["usable_ratio"] <= 1
        and _finite_at_least(coverage.get("usable_fps"), 10)
        and isinstance(clock_bases, list)
        and bool(clock_bases)
        and not any(basis in ("synthetic", "unknown") for basis in clock_bases)
        and provenance.get("measurement_basis") == "VISIBLE_SEQUENCE"
    )


def evaluate_dataset(manifest_path: Path, data_root: Path | None = None) -> dict:
    from packages.cv.signoff_cv.evaluate import evaluate_cycles
    from packages.cv.signoff_cv.pipeline import analyze_video

    context = validate_frozen_inputs(manifest_path, data_root)
    manifest = context["manifest"]
    reasons = list(context["reasons"])
    scored, idle_scored = [], []
    for item in context["validated"]:
        row, inputs, profile = item["row"], item["inputs"], item["profile"]
        if row["split"] not in ("test", "ood"):
            continue
        # Labels and annotation artifacts are intentionally not inference arguments.
        result = analyze_video(inputs["video"], profile=profile, clock_path=inputs["clock"])
        score = evaluate_cycles(
            item["reference"],
            result["detected_cycles"],
            reference_id=row["session_id"],
            reference_revision=row["reference_sha256"],
        )
        coverage, provenance = result.get("coverage", {}), result.get("provenance", {})
        if item["inactivity_reference"]:
            intervals = [
                {"cycle_id": f"idle-{index}", "start_ms": row["start_ms"], "end_ms": row["end_ms"]}
                for index, row in enumerate(result.get("idle_intervals", []), 1)
            ]
            idle_score = evaluate_cycles(
                item["inactivity_reference"],
                intervals,
                reference_id=row["session_id"] + "-idle",
                reference_revision=row["inactivity_reference_sha256"],
            )
            idle_scored.append(
                {
                    "session_id": row["session_id"],
                    "source_sha256": row["video_sha256"],
                    "reference_sha256": row["inactivity_reference_sha256"],
                    "score": idle_score,
                    "coverage": coverage,
                    "annotation_artifacts": item["inactivity_artifacts"],
                    "annotation_agreement": item["inactivity_agreement"],
                    "adjudication": row["inactivity_adjudication"],
                    "accuracy_claim": "descriptive_observation_no_new_acceptance_threshold",
                }
            )
        passed = (
            score_passes(score, coverage, provenance) and profile["detector"]["method"] == "aruco"
        )
        scored.append(
            {
                "session_id": row["session_id"],
                "split": row["split"],
                "score": score,
                "coverage": coverage,
                "provenance": provenance,
                "analysis_hash": result.get("analysis_hash"),
                "passed": passed,
                "video_duration_ms": result.get("video_duration_ms"),
                "detected_cycles_hash": measurement_digest(result["detected_cycles"]),
                "input_hashes": {
                    key: row[key] for key in ("video_sha256", "reference_sha256", "profile_sha256")
                },
                "sources": item["sources"],
                "annotation_artifacts": item["annotation_artifacts"],
                "annotation_agreement": item["annotation_agreement"],
                "adjudication": row["adjudication"],
            }
        )
    nominal = [item for item in scored if item["split"] == "test"]
    ood = [item for item in scored if item["split"] == "ood"]
    if not nominal or not all(item["passed"] for item in nominal):
        reasons.append("one or more nominal measurement gates did not pass")
    scope = {
        "profile_hash": None,
        "configuration_hash": None,
        "code_sha": None,
        "protocol_id": context["protocol_id"],
        "protocol_hash": context["protocol_hash"],
    }
    for key in ("profile_hash", "configuration_hash", "code_sha"):
        values = {item["provenance"].get(key) for item in nominal}
        if len(values) != 1 or None in values:
            reasons.append(f"nominal measurement scope lacks one identified {key}")
        else:
            scope[key] = next(iter(values))
    cycle_reasons = [
        reason for reason in reasons if "inactivity" not in reason and "soak" not in reason
    ]
    inactivity = {
        "available": context["inactivity_episodes"] >= 100 and bool(idle_scored),
        "reference_episodes": context["inactivity_episodes"],
        "sessions": idle_scored,
        "accuracy_claim": "descriptive_observation_no_new_acceptance_threshold",
        "owner_review_required": True,
    }
    inactivity["review_hash"] = _digest(inactivity)
    if not inactivity["available"]:
        reasons.append("independent inactivity scoring evidence is incomplete")
    reasons.extend(validate_soak_receipt(context["soak"], scope, context["soak_sources"]))
    qualifying = not reasons
    receipt = {
        "schema_version": 2,
        "kind": "physical_camera_qualification",
        "dataset_id": context["dataset_id"],
        "source_kind": manifest.get("source_kind", "unknown"),
        "manifest_sha256": _sha(Path(manifest_path)),
        "scope": scope,
        "split_summary": context["split_summary"],
        "inactivity": inactivity,
        "soak": context["soak"],
        "soak_sources": context["soak_sources"],
        "cycle_qualification": {
            "status": "qualified" if not cycle_reasons else "pending",
            "reasons": cycle_reasons,
        },
        "qualification": {
            "status": "qualified" if qualifying else "pending",
            "physical": qualifying,
            "physically_qualified": qualifying,
            "reasons": reasons,
            "nominal_reference_cycles": context["split_summary"]["test"]["reference_cycles"],
            "ood_reference_cycles": context["split_summary"]["ood"]["reference_cycles"],
            "unsupported_conditions": [item["session_id"] for item in ood if not item["passed"]],
            "annotators": manifest.get("annotators", []),
            "annotator_kinds": manifest.get("annotator_kinds", {}),
            "capture_attested_by": manifest.get("capture_attested_by"),
            "source_attestation_level": "owner_attested",
            "external_validation": False,
        },
        "sessions": scored,
    }
    digest = qualification_receipt_hash(receipt)
    receipt["receipt_hash"] = digest
    receipt["qualification"]["receipt_hash"] = digest
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = evaluate_dataset(args.manifest, args.data_root)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"qualification rejected: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    print(report["qualification"]["status"], args.out)
    return 0 if report["qualification"]["physically_qualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
