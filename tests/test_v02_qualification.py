"""Qualification/release plumbing tests with mocked observations, not physical evidence."""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from packages.cv.signoff_cv.evaluate import evaluate_cycles
from packages.domain.profiles import validate_profile
from packages.metrics.signoff_metrics import session_metrics
from packages.reporting.signoff_report import build_report, report_hash
from tools.qualify_dataset import (
    _digest,
    _sha,
    evaluate_dataset,
    qualification_receipt_hash,
    score_passes,
    validate_frozen_inputs,
)
from tools.release_gate import evaluate_release


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2))
    return _sha(path)


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    return _sha(path)


@pytest.fixture()
def frozen(tmp_path, monkeypatch):
    """Tiny input bytes + mocked vision isolate gate semantics and keep tests fast."""
    profile = validate_profile({"profile_id": "station"})
    _write(tmp_path / "profile.json", profile)
    protocol = {"protocol_id": "pick-place-v1", "contract": "signoff-camera-v0.2"}
    protocol_hash = _write(tmp_path / "protocol.json", protocol)
    annotators = ["owner", "assistant"]
    manifest = {
        "schema_version": 2, "dataset_id": "fixture-qualification",
        "source_kind": "physical_robot_station", "capture_attested_by": "fixture-owner",
        "frozen_at": "2026-10-13T00:00:00+07:00", "annotators": annotators,
        "annotator_kinds": {"owner": "human", "assistant": "ai_assisted"},
        "protocol_id": "pick-place-v1", "protocol": "protocol.json",
        "protocol_sha256": protocol_hash, "sessions": []}
    references, results = {}, {}
    for split, sessions in (("development", 6), ("validation", 2), ("test", 2), ("ood", 1)):
        for index in range(sessions):
            sid = f"{split}-{index}"
            n = 100 if split == "ood" else 60
            video = tmp_path / f"{sid}.mkv"
            video.write_bytes(f"mocked media bytes for {sid}".encode())
            labels = [{"cycle": i + 1, "start_ms": 1000 + i * 2000,
                       "end_ms": 2000 + i * 2000, "outcome": "UNKNOWN"} for i in range(n)]
            reference_path = tmp_path / f"{sid}.reference.jsonl"
            reference_hash = _jsonl(reference_path, labels)
            row = {"session_id": sid, "split": split, "video": video.name,
                   "video_sha256": _sha(video), "reference": reference_path.name,
                   "reference_sha256": reference_hash, "profile": "profile.json",
                   "profile_sha256": _sha(tmp_path / "profile.json"),
                   "profile_hash": profile["profile_hash"]}
            if split in ("test", "ood"):
                annotations = []
                for name in annotators:
                    path = tmp_path / f"{sid}.{name}.jsonl"
                    rows = [{**label, "annotator": name, "session": sid} for label in labels]
                    digest = _jsonl(path, rows)
                    annotations.append({"annotator": name, "file": path.name, "sha256": digest,
                                        "independently_labeled": True})
                row.update({"annotations": annotations, "adjudication": {
                    "adjudicator": "owner", "reference_sha256": reference_hash,
                    "annotation_sha256": [item["sha256"] for item in annotations],
                    "reason": "Fixture independently labeled rows agree."}})
            manifest["sessions"].append(row)
            references[sid] = labels
            cycles = [{"cycle_id": f"c{i + 1}", "cycle": i + 1, "start_ms": row["start_ms"],
                       "end_ms": row["end_ms"], "start_frame": i * 20, "end_frame": i * 20 + 10,
                       "status": "COMPLETE", "basis": "VISIBLE_SEQUENCE"}
                      for i, row in enumerate(labels)]
            duration = 1000 + n * 2000
            results[video.name] = {
                "detected_cycles": cycles, "video_duration_ms": duration,
                "coverage": {"usable_ratio": 1.0, "usable_fps": 12, "observed_ms": duration,
                             "unknown_ms": 0},
                "provenance": {"profile_hash": profile["profile_hash"],
                               "configuration_hash": "fixture-config",
                               "code_sha": "fixture-code", "timestamp_basis": ["capture_sidecar"],
                               "observed_fps": 30, "measurement_basis": "VISIBLE_SEQUENCE"},
                "analysis_hash": _digest(cycles)}
    path = tmp_path / "manifest.json"
    _write(path, manifest)
    calls = []

    def analyze(path, profile=None, clock_path=None):
        calls.append((path, profile, clock_path))
        return copy.deepcopy(results[Path(path).name])

    monkeypatch.setattr("packages.cv.signoff_cv.pipeline.analyze_video", analyze)
    return {"root": tmp_path, "path": path, "manifest": manifest, "results": results,
            "calls": calls, "references": references}


def _save_manifest(frozen):
    _write(frozen["path"], frozen["manifest"])


def _receipts(frozen):
    """Create bound test receipts from mocked observations, not real hardware."""
    receipt = evaluate_dataset(frozen["path"], frozen["root"])
    qualification_path = frozen["root"] / "qualification.json"
    _write(qualification_path, receipt)
    sid = "test-0"
    result = frozen["results"][sid + ".mkv"]
    metrics = session_metrics([], result["detected_cycles"], [],
                              session_duration_ms=result["video_duration_ms"],
                              coverage=result["coverage"])
    row = next(row for row in frozen["manifest"]["sessions"] if row["session_id"] == sid)
    report = build_report({"session_id": sid, "video_sha256": row["video_sha256"]},
                          metrics, detected_cycles=result["detected_cycles"],
                          provenance=result["provenance"], run_id="run-owner")
    report_path = frozen["root"] / "report.json"
    _write(report_path, report)
    pilot = {
        "schema_version": 2, "pilot_type": "owner", "reviewer_kind": "OWNER",
        "reviewer": "Fixture owner", "decision": "ACCEPT",
        "reason": "Fixture workflow reviewed with assistant; not external validation.",
        "run_id": report["run_id"], "report_hash": report["report_hash"],
        "report_sha256": _sha(report_path), "qualification_sha256": _sha(qualification_path),
        "manifest_sha256": _sha(frozen["path"])}
    pilot_path = frozen["root"] / "owner-pilot.json"
    _write(pilot_path, pilot)
    return {"qualification_path": qualification_path, "pilot_path": pilot_path,
            "report_path": report_path, "manifest_path": frozen["path"],
            "data_root": frozen["root"]}, receipt, report, pilot


def test_mocked_qualification_records_scope_lineage_kinds_and_uncertainty(frozen):
    receipt = evaluate_dataset(frozen["path"], frozen["root"])
    assert receipt["qualification"]["status"] == "qualified"
    assert receipt["qualification"]["physical"] is True
    assert receipt["qualification"]["physically_qualified"] is True
    assert receipt["qualification"]["external_validation"] is False
    assert receipt["qualification"]["annotator_kinds"]["assistant"] == "ai_assisted"
    assert receipt["receipt_hash"] == qualification_receipt_hash(receipt)
    assert receipt["qualification"]["receipt_hash"] == receipt["receipt_hash"]
    assert receipt["split_summary"]["development"] == {"sessions": 6, "reference_cycles": 360}
    assert receipt["split_summary"]["test"] == {"sessions": 2, "reference_cycles": 120}
    assert receipt["scope"]["protocol_hash"] == frozen["manifest"]["protocol_sha256"]
    assert receipt["sessions"][0]["annotation_agreement"][0]["common_cycles"] == 60
    assert receipt["sessions"][0]["score"]["uncertainty"]["recall"]["n"] == 60
    assert all(call[0].suffix == ".mkv" for call in frozen["calls"])


def test_same_split_duplicate_footage_cannot_inflate_denominators(frozen):
    tests = [row for row in frozen["manifest"]["sessions"] if row["split"] == "test"]
    tests[1]["video"], tests[1]["video_sha256"] = tests[0]["video"], tests[0]["video_sha256"]
    _save_manifest(frozen)
    with pytest.raises(ValueError, match="duplicate footage"):
        evaluate_dataset(frozen["path"], frozen["root"])
    assert not frozen["calls"]


def test_development_validation_minima_are_required(frozen):
    frozen["manifest"]["sessions"] = [row for row in frozen["manifest"]["sessions"]
                                      if row["split"] not in ("development", "validation")]
    _save_manifest(frozen)
    receipt = evaluate_dataset(frozen["path"], frozen["root"])
    assert receipt["qualification"]["status"] == "pending"
    assert not receipt["qualification"]["physical"]
    assert any("development requires" in reason for reason in receipt["qualification"]["reasons"])
    assert any("validation requires" in reason for reason in receipt["qualification"]["reasons"])


def test_declared_names_without_independent_artifacts_rejected(frozen):
    row = next(row for row in frozen["manifest"]["sessions"] if row["split"] == "test")
    row.pop("annotations")
    _save_manifest(frozen)
    with pytest.raises(ValueError, match="independent annotation artifacts"):
        evaluate_dataset(frozen["path"], frozen["root"])


def test_adjudication_must_bind_all_independent_artifacts(frozen):
    row = next(row for row in frozen["manifest"]["sessions"] if row["split"] == "test")
    row["adjudication"]["annotation_sha256"] = row["adjudication"]["annotation_sha256"][:1]
    _save_manifest(frozen)
    with pytest.raises(ValueError, match="adjudication"):
        evaluate_dataset(frozen["path"], frozen["root"])


def test_unknown_reviewer_kind_is_not_independent_validation(frozen):
    frozen["manifest"]["annotator_kinds"]["assistant"] = "unidentified"
    _save_manifest(frozen)
    with pytest.raises(ValueError, match="identity/kind"):
        evaluate_dataset(frozen["path"], frozen["root"])


def test_usable_observation_rate_not_total_fps_gates_qualification(frozen):
    frozen["results"]["test-0.mkv"]["coverage"]["usable_fps"] = 9.9
    receipt = evaluate_dataset(frozen["path"], frozen["root"])
    assert receipt["qualification"]["status"] == "pending"
    assert not receipt["sessions"][0]["passed"]
    assert receipt["sessions"][0]["provenance"]["observed_fps"] == 30


def test_unfrozen_auto_loaded_sidecar_clock_rejected_before_inference(frozen):
    clock = frozen["root"] / "test-0.clock.jsonl"
    clock.write_text('{"frame_no":0,"ms":0}\n')
    with pytest.raises(ValueError, match="clock_sha256"):
        evaluate_dataset(frozen["path"], frozen["root"])
    assert not frozen["calls"]


def test_frozen_sidecar_is_passed_and_tampering_rejected(frozen):
    clock = frozen["root"] / "test-0.clock.jsonl"
    clock.write_text('{"frame_no":0,"ms":0}\n')
    row = next(row for row in frozen["manifest"]["sessions"] if row["session_id"] == "test-0")
    row.update({"clock": clock.name, "clock_sha256": _sha(clock)})
    _save_manifest(frozen)
    evaluate_dataset(frozen["path"], frozen["root"])
    assert frozen["calls"][0][2] == clock
    clock.write_text('{"frame_no":0,"ms":999}\n')
    with pytest.raises(ValueError, match="clock hash"):
        validate_frozen_inputs(frozen["path"], frozen["root"])


def test_shared_recording_segments_cannot_hide_duplicate_footage(frozen):
    row = next(row for row in frozen["manifest"]["sessions"] if row["session_id"] == "test-0")
    segment = frozen["root"] / row["video"]
    clock = segment.with_suffix(".clock.jsonl")
    clock.write_text('{"frame_no":0,"ms":0}\n')
    recording = {"status": "complete", "segments": [
        {"file": segment.name, "sha256": _sha(segment),
         "clock_file": clock.name, "clock_sha256": _sha(clock)}]}
    for index in (0, 1):
        path = frozen["root"] / f"wrapped-{index}.manifest.json"
        _write(path, {**recording, "session_label": index})
        candidate = next(row for row in frozen["manifest"]["sessions"]
                         if row["session_id"] == f"test-{index}")
        candidate.update({"video": path.name, "video_sha256": _sha(path)})
    _save_manifest(frozen)
    with pytest.raises(ValueError, match="duplicate media segments"):
        validate_frozen_inputs(frozen["path"], frozen["root"])


def test_simulated_capture_remains_pending(frozen):
    frozen["manifest"]["source_kind"] = "simulation"
    _save_manifest(frozen)
    receipt = evaluate_dataset(frozen["path"], frozen["root"])
    assert not receipt["qualification"]["physical"]
    assert receipt["qualification"]["status"] == "pending"


def test_legacy_matcher_or_false_score_denominators_never_qualify():
    score = evaluate_cycles([{"cycle": 1, "start_ms": 100, "end_ms": 200}],
                            [{"cycle_id": "c", "start_ms": 100, "end_ms": 200}],
                            reference_id="ref")
    coverage = {"usable_ratio": 1, "usable_fps": 30}
    provenance = {"timestamp_basis": ["decoded_pts"], "measurement_basis": "VISIBLE_SEQUENCE"}
    assert score_passes(score, coverage, provenance)
    score["matcher"] = "legacy_start_only_greedy"
    assert not score_passes(score, coverage, provenance)
    score["matcher"] = "two_boundary_max_cardinality_min_error"
    score["false_negatives"] = 10
    assert not score_passes(score, coverage, provenance)


def test_mocked_owner_acceptance_bound_to_all_receipts_is_eligible(frozen):
    arguments, receipt, report, pilot = _receipts(frozen)
    result = evaluate_release(**arguments)
    assert result["status"] == "READY", result["blockers"]
    assert result["pilot_kind"] == "OWNER"
    assert result["external_validation"] is False
    assert result["report_hash"] == report["report_hash"]
    assert result["qualification_receipt_hash"] == receipt["receipt_hash"]
    assert result["evidence_hashes"]["report_sha256"] == pilot["report_sha256"]
    # Physical scope is attached through an accepted receipt; original report stays immutable.
    assert report["qualification"]["status"] == "provisional"


def test_missing_receipts_remain_release_candidate():
    result = evaluate_release()
    assert result["status"] == "PENDING"
    assert not result["physical_ready"]
    assert result["release_channel"] == "release_candidate"


@pytest.mark.parametrize("field", ["report_sha256", "qualification_sha256", "manifest_sha256",
                                   "report_hash", "run_id"])
def test_owner_decision_requires_exact_receipt_bindings(frozen, field):
    arguments, _, _, pilot = _receipts(frozen)
    pilot[field] = "wrong"
    _write(arguments["pilot_path"], pilot)
    result = evaluate_release(**arguments)
    assert result["status"] == "PENDING"
    assert any(field in reason or "measurement run" in reason for reason in result["blockers"])


def test_prototype_acceptance_without_physical_bindings_cannot_release(frozen):
    arguments, _, _, pilot = _receipts(frozen)
    pilot.pop("qualification_sha256")
    pilot.pop("manifest_sha256")
    _write(arguments["pilot_path"], pilot)
    assert evaluate_release(**arguments)["status"] == "PENDING"


def test_external_label_does_not_replace_owner_acceptance(frozen):
    arguments, _, _, pilot = _receipts(frozen)
    pilot["reviewer_kind"] = "EXTERNAL"
    _write(arguments["pilot_path"], pilot)
    result = evaluate_release(**arguments)
    assert result["status"] == "PENDING"
    assert result["pilot_kind"] is None


def test_changed_physical_input_invalidates_release_receipt(frozen):
    arguments, _, _, _ = _receipts(frozen)
    (frozen["root"] / "test-0.mkv").write_bytes(b"changed")
    result = evaluate_release(**arguments)
    assert result["status"] == "PENDING"
    assert any("current frozen inputs" in reason for reason in result["blockers"])


def test_valid_rehashed_report_with_wrong_instrument_or_metrics_still_rejected(frozen):
    arguments, _, report, pilot = _receipts(frozen)
    report["provenance"]["configuration_hash"] = "wrong"
    report["metrics"]["cycle_durations"]["mean_ms"] = 999999
    report["report_hash"] = report_hash(report)
    _write(arguments["report_path"], report)
    pilot.update({"report_hash": report["report_hash"],
                  "report_sha256": _sha(arguments["report_path"])})
    _write(arguments["pilot_path"], pilot)
    result = evaluate_release(**arguments)
    assert result["status"] == "PENDING"
    assert any("configuration_hash" in reason for reason in result["blockers"])
    assert any("cycle timing" in reason for reason in result["blockers"])


def test_release_cli_missing_artifacts_exits_pending():
    import sys
    process = subprocess.run([sys.executable, "tools/release_gate.py"], capture_output=True,
                             text=True, check=False)
    assert process.returncode == 2
    assert json.loads(process.stdout)["status"] == "PENDING"
