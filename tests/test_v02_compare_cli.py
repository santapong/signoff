"""M7 CLI consumes verified report snapshots and preserves provisional status."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from packages.metrics.signoff_metrics import session_metrics
from packages.reporting.signoff_report import build_report


def test_compare_cli_exports_and_rejects_tampering_protocol_and_path_mismatch(tmp_path):
    manifest = {"protocol_id": "demo-v1", "protocol_hash": "frozen-camera-protocol", "entries": []}
    provenance = {
        "profile_hash": "station-profile",
        "configuration_hash": "same-config",
        "code_sha": "same-code",
        "timestamp_basis": "synthetic",
        "protocol_id": manifest["protocol_id"],
        "protocol_hash": manifest["protocol_hash"],
    }
    for name, duration in (("scripted", 1000), ("learned", 1200)):
        cycle = {
            "cycle_id": name,
            "status": "COMPLETE",
            "start_ms": 1000,
            "end_ms": 1000 + duration,
        }
        metrics = session_metrics(
            [], [cycle], [], session_duration_ms=5000, coverage={"observed_ms": 5000}
        )
        report = build_report(
            {"session_id": name, "source_kind": "simulation"},
            metrics,
            detected_cycles=[cycle],
            provenance=provenance,
            run_id=name,
        )
        (tmp_path / f"{name}.json").write_text(json.dumps(report))
        manifest["entries"].append(
            {
                "report_path": f"{name}.json",
                "experiment_id": name,
                "controller_label": name,
                "policy_label": name,
            }
        )
    manifest_path = tmp_path / "experiments.json"
    manifest_path.write_text(json.dumps(manifest))
    script = Path(__file__).resolve().parents[1] / "tools" / "compare_experiments.py"
    output = tmp_path / "comparison"
    command = [sys.executable, str(script), str(manifest_path), "--out", str(output)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "provisional"
    result = json.loads((output / "comparison.json").read_text())
    assert result["status"] == "provisional"
    assert "physical_qualification_receipt_unavailable" in result["provisional_reasons"]
    assert len(result["experiments"]) == 2
    assert all(row["success_rate"] is None for row in result["experiments"])
    assert (output / "comparison.csv").is_file()
    assert "provisional" in (output / "comparison.md").read_text()
    # A conflicting protocol is rejected before any additional export is created.
    manifest["protocol_hash"] = "different-protocol"
    manifest_path.write_text(json.dumps(manifest))
    rejected = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rejected.returncode == 2 and "protocol" in rejected.stderr
    manifest["protocol_hash"] = "frozen-camera-protocol"
    manifest["entries"][0]["report_path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest))
    rejected = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rejected.returncode == 2 and "escapes" in rejected.stderr
    manifest["entries"][0]["report_path"] = "scripted.json"
    manifest_path.write_text(json.dumps(manifest))
    path = tmp_path / "scripted.json"
    report = json.loads(path.read_text())
    report["metrics"]["cycle_durations"]["n"] = 100
    path.write_text(json.dumps(report))
    rejected = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rejected.returncode == 2 and "intact" in rejected.stderr
    assert json.loads((output / "comparison.json").read_text()) == result
