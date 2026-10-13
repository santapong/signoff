"""Instrument gate fixtures are mocked plumbing, never actual physical qualification."""

from __future__ import annotations

import copy
import json

import pytest
from test_v02_qualification import _jsonl, _receipts, _save_manifest, _write
from test_v02_qualification import frozen as legacy_frozen

from tools.qualify_dataset import _digest, _sha, evaluate_dataset, validate_frozen_inputs
from tools.release_gate import evaluate_release

frozen = legacy_frozen


@pytest.fixture
def instrument(frozen):
    for row in frozen["manifest"]["sessions"]:
        if row["split"] != "test":
            continue
        sid = row["session_id"]
        labels = [
            {
                "cycle": i + 1,
                "start_ms": 100 + i * 2000,
                "end_ms": 800 + i * 2000,
                "outcome": "UNKNOWN",
            }
            for i in range(50)
        ]
        path = frozen["root"] / f"{sid}-idle.jsonl"
        reference_hash = _jsonl(path, labels)
        descriptors = []
        for reviewer in ("owner", "assistant"):
            artifact = frozen["root"] / f"{sid}-idle-{reviewer}.jsonl"
            _jsonl(artifact, [{**label, "annotator": reviewer, "session": sid} for label in labels])
            descriptors.append(
                {
                    "annotator": reviewer,
                    "file": artifact.name,
                    "sha256": _sha(artifact),
                    "independently_labeled": True,
                }
            )
        row.update(
            inactivity_reference=path.name,
            inactivity_reference_sha256=reference_hash,
            inactivity_annotations=descriptors,
            inactivity_adjudication={
                "adjudicator": "owner",
                "reason": "Source inactivity boundaries reviewed.",
                "reference_sha256": reference_hash,
                "annotation_sha256": [item["sha256"] for item in descriptors],
            },
        )
        frozen["results"][row["video"]]["idle_intervals"] = labels
    segment = frozen["root"] / "soak.mkv"
    segment.write_bytes(b"mocked soak pixels")
    clock = frozen["root"] / "soak.clock.jsonl"
    count = 72000
    clock.write_text("".join(json.dumps({"frame": i, "ms": i * 100}) + "\n" for i in range(count)))
    recording = {
        "status": "complete",
        "end_ms": 7200000,
        "segments": [
            {
                "file": segment.name,
                "sha256": _sha(segment),
                "clock_file": clock.name,
                "clock_sha256": _sha(clock),
            }
        ],
    }
    media = frozen["root"] / "soak.manifest.json"
    _write(media, recording)
    prototype = frozen["results"]["test-0.mkv"]["provenance"]
    receipt = {
        "schema_version": 3,
        "kind": "live_camera_soak",
        "source_kind": "physical_robot_station",
        "capture_attested_by": "fixture-owner",
        "session_id": "mock-soak",
        "scope": {
            key: prototype[key] for key in ("profile_hash", "configuration_hash", "code_sha")
        },
        "duration_ms": 7200000,
        "coverage": {"usable_ratio": 1, "usable_fps": 10},
        "observation_count": count,
        "real_observations": True,
        "latency": {
            "clock_basis": "host_ingest_to_publication",
            "sample_count": count,
            "qualified_event_count": 600,
            "p95_ms": 100,
            "max_ms": 200,
        },
        "recording": {"manifest_sha256": _sha(media), "integrity_verified": True},
        "source_hashes": recording["segments"],
    }
    receipt["receipt_hash"] = _digest(receipt)
    soak = frozen["root"] / "soak-receipt.json"
    _write(soak, receipt)
    frozen["manifest"].update(
        soak=soak.name, soak_sha256=_sha(soak), soak_media=media.name, soak_media_sha256=_sha(media)
    )
    _save_manifest(frozen)
    frozen["soak_receipt"] = receipt
    return frozen


def _accepted(instrument):
    arguments, receipt, report, pilot = _receipts(instrument)
    pilot.update(
        inactivity_acknowledged=True, inactivity_review_hash=receipt["inactivity"]["review_hash"]
    )
    _write(arguments["pilot_path"], pilot)
    return arguments, receipt, report, pilot


def test_full_mocked_instrument_receipt_and_owner_acknowledgment_is_eligible(instrument):
    arguments, receipt, _, _ = _accepted(instrument)
    assert receipt["qualification"]["status"] == "qualified", receipt["qualification"]["reasons"]
    assert receipt["inactivity"]["reference_episodes"] == 100
    assert receipt["inactivity"]["sessions"][0]["accuracy_claim"].startswith("descriptive")
    result = evaluate_release(**arguments)
    assert result["status"] == "READY", result["blockers"]


def test_inactivity_score_is_descriptive_without_new_accuracy_threshold(instrument):
    instrument["results"]["test-0.mkv"]["idle_intervals"] = []
    arguments, receipt, _, _ = _accepted(instrument)
    assert receipt["inactivity"]["sessions"][0]["score"]["f1"] == 0
    assert receipt["inactivity"]["accuracy_claim"].startswith("descriptive")
    assert evaluate_release(**arguments)["status"] == "READY"


def test_cycle_only_receipt_cannot_become_full_instrument_release(frozen):
    arguments, receipt, _, _ = _receipts(frozen)
    assert receipt["cycle_qualification"]["status"] == "qualified"
    assert receipt["qualification"]["status"] == "pending"
    assert evaluate_release(**arguments)["status"] == "PENDING"


@pytest.mark.parametrize(
    "changes",
    [
        {"duration_ms": 1000},
        {"source_kind": "simulation"},
        {"real_observations": False},
        {
            "latency": {
                "clock_basis": "host_ingest_to_publication",
                "sample_count": 72000,
                "qualified_event_count": 600,
                "p95_ms": 1001,
                "max_ms": 2000,
            }
        },
    ],
)
def test_missing_short_or_simulated_soak_never_satisfies_physical_gate(instrument, changes):
    receipt = copy.deepcopy(instrument["soak_receipt"])
    receipt.update(changes)
    receipt.pop("receipt_hash")
    receipt["receipt_hash"] = _digest(receipt)
    path = instrument["root"] / instrument["manifest"]["soak"]
    _write(path, receipt)
    instrument["manifest"]["soak_sha256"] = _sha(path)
    _save_manifest(instrument)
    if "duration_ms" in changes:
        with pytest.raises(ValueError, match="recording clock"):
            evaluate_dataset(instrument["path"], instrument["root"])
    else:
        result = evaluate_dataset(instrument["path"], instrument["root"])
        assert result["qualification"]["status"] == "pending"


def test_tampered_inactivity_or_soak_original_prevents_release(instrument):
    arguments, _, _, _ = _accepted(instrument)
    row = next(row for row in instrument["manifest"]["sessions"] if row["split"] == "test")
    (instrument["root"] / row["inactivity_reference"]).write_text("changed")
    assert evaluate_release(**arguments)["status"] == "PENDING"
    with pytest.raises(ValueError, match="hash"):
        validate_frozen_inputs(instrument["path"], instrument["root"])


def test_owner_must_acknowledge_exact_inactivity_evidence(instrument):
    arguments, _, _, pilot = _accepted(instrument)
    pilot["inactivity_review_hash"] = "different"
    _write(arguments["pilot_path"], pilot)
    result = evaluate_release(**arguments)
    assert result["status"] == "PENDING"
    assert any("inactivity" in reason for reason in result["blockers"])
