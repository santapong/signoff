from typing import Any

import pytest

from packages.domain.annotations import (
    make_annotation,
    read_annotations,
    write_annotations,
)
from packages.domain.experiment_registry import (
    get_experiment,
    record_result,
    register_experiment,
)
from packages.domain.session import ObservationSession, init_session_dirs

VALID: dict[str, Any] = dict(
    session_id="ses_X", cycle=1, cycle_start_ms=100.0, cycle_end_ms=500.0,
    video_duration_ms=10000.0,
)


def test_valid_annotation_roundtrip(tmp_path):
    ann = make_annotation(annotator="tester", **VALID)
    path = tmp_path / "ann.jsonl"
    write_annotations(path, [ann])
    assert read_annotations(path) == [ann]


def test_failed_requires_failure_type():
    with pytest.raises(ValueError):
        make_annotation(outcome="FAILED", **VALID)


def test_rejects_inverted_times():
    bad = {**VALID, "cycle_start_ms": 600.0, "cycle_end_ms": 500.0}
    with pytest.raises(ValueError):
        make_annotation(**bad)


def test_rejects_beyond_video():
    with pytest.raises(ValueError):
        make_annotation(**{**VALID, "cycle_end_ms": 99999.0})


def test_registry_append_only(tmp_path):
    reg = tmp_path / "registry.jsonl"
    entry = {
        "experiment_id": "EXP-CYCLE-001", "git_sha": "abc", "dataset_version": "v0",
        "camera_profile": "cam1", "algorithm": "x", "config": {},
        "hypothesis": "h", "primary_metric": "f1",
    }
    register_experiment(reg, entry)
    with pytest.raises(ValueError):  # duplicate registration blocked
        register_experiment(reg, entry)
    record_result(reg, "EXP-CYCLE-001", {"f1": 0.93})
    record_result(reg, "EXP-CYCLE-001", {"f1": 0.95})  # results append freely
    assert get_experiment(reg, "EXP-CYCLE-001")["hypothesis"] == "h"
    lines = reg.read_text().splitlines()
    assert len(lines) == 3


def test_session_manifest_written_once(tmp_path):
    s = ObservationSession(experiment_id="EXP-CYCLE-001")
    d1 = init_session_dirs(tmp_path, s)
    manifest = d1 / "session.json"
    first = manifest.read_text()
    init_session_dirs(tmp_path, s)
    assert manifest.read_text() == first
