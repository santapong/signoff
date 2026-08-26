"""Append-only experiment registry (SDD §89).

Each experiment is one JSON line: register before data collection, update the
same line's result/conclusion fields after — history preserved via `updated_at`
and never deleting prior entries.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

_REQUIRED_FIELDS = {
    "experiment_id",
    "git_sha",
    "dataset_version",
    "camera_profile",
    "algorithm",
    "config",
    "hypothesis",
    "primary_metric",
}

_LOCK = Lock()


def register_experiment(registry_path: Path, entry: dict) -> dict:
    missing = _REQUIRED_FIELDS - entry.keys()
    if missing:
        raise ValueError(f"experiment entry missing fields: {sorted(missing)}")
    with _LOCK:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        existing = _find(registry_path, entry["experiment_id"])
        if existing is not None:
            raise ValueError(
                f"experiment {entry['experiment_id']} already registered"
            )
        with registry_path.open("a") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def record_result(registry_path: Path, experiment_id: str, result: dict) -> None:
    """Append a result line referencing a registered experiment."""
    with _LOCK:
        if _find(registry_path, experiment_id) is None:
            raise ValueError(f"unknown experiment {experiment_id}")
        payload = {"experiment_id": experiment_id, "kind": "result", **result}
        with registry_path.open("a") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")


def get_experiment(registry_path: Path, experiment_id: str) -> dict | None:
    return _find(registry_path, experiment_id)


def _find(registry_path: Path, experiment_id: str) -> dict | None:
    if not registry_path.exists():
        return None
    for line in registry_path.read_text().splitlines():
        obj = json.loads(line)
        if obj.get("experiment_id") == experiment_id and obj.get("kind") != "result":
            return obj
    return None
