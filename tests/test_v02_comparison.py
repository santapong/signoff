"""Secondary comparison exports preserve provisional evidence boundaries."""
import copy
import json

import pytest

from packages.domain.comparison import build_comparison, write_comparison
from packages.metrics.signoff_metrics import session_metrics
from packages.reporting.signoff_report import build_report


def _entry(name, *, qualification=None, provenance=None):
    cycle = {"cycle_id": "c1", "start_ms": 1000, "end_ms": 2000}
    metrics = session_metrics([], [cycle], [], session_duration_ms=60000,
                              coverage={"observed_ms": 60000})
    report = build_report({"session_id": name}, metrics, run_id=f"run_{name}",
                          detected_cycles=[cycle], qualification=qualification,
                          provenance=provenance)
    return {"experiment_id": name, "controller_label": "scripted",
            "policy_label": name, "report": report}


def _compare(entries):
    return build_comparison(entries, protocol_id="pick-place-v1", protocol_hash="frozen-protocol")


def test_provisional_comparison_consumes_frozen_reports_only(tmp_path):
    entries = [_entry("b"), _entry("a")]
    originals = copy.deepcopy(entries)
    result = _compare(entries)
    assert entries == originals
    assert result["status"] == "provisional"
    assert result["experiments"][0]["experiment_id"] == "a"
    assert result["experiments"][0]["success_rate"] is None
    assert result["experiments"][0]["detection_f1"] is None
    assert result == _compare(list(reversed(entries)))
    outputs = write_comparison(tmp_path, result)
    assert json.loads(outputs["json"].read_text()) == result
    assert "provisional" in outputs["markdown"].read_text()


def test_supplied_physical_receipts_and_complete_provenance_are_required():
    provenance = {"profile_hash": "profile", "configuration_hash": "config",
                  "timestamp_basis": "capture_clock", "code_sha": "code"}
    receipt = {"status": "qualified", "physical": True, "receipt_hash": "receipt"}
    result = _compare([_entry("a", provenance=provenance, qualification=receipt),
                       _entry("b", provenance=provenance, qualification=receipt)])
    assert result["status"] == "qualified"
    assert result["provisional_reasons"] == []


def test_report_without_physical_receipt_remains_provisional():
    result = _compare([_entry("a", qualification={"status": "qualified"}),
                       _entry("b", qualification={"status": "qualified"})])
    assert result["status"] == "provisional"


def test_mismatched_instrument_rejected():
    with pytest.raises(ValueError, match="instrument differs"):
        _compare([_entry("a", provenance={"profile_hash": "one"}),
                  _entry("b", provenance={"profile_hash": "two"})])


def test_tampered_report_rejected():
    first = _entry("a")
    first["report"]["metrics"]["cycle_durations"]["n"] = 100
    with pytest.raises(ValueError, match="intact"):
        _compare([first, _entry("b")])


def test_duplicate_report_rejected():
    first = _entry("a")
    with pytest.raises(ValueError, match="twice"):
        _compare([first, copy.deepcopy(first)])


def test_protocol_mismatch_rejected():
    first = _entry("a")
    first["protocol_id"] = "different"
    with pytest.raises(ValueError, match="protocol ID mismatch"):
        _compare([first, _entry("b")])
