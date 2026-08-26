import pytest

from robotics.elexhub.logging.ground_truth import GroundTruthLogger


def test_logs_session_start_and_end(tmp_path):
    logger = GroundTruthLogger(tmp_path / "gt" / "events.jsonl")
    logger.log("CYCLE_START", cycle=1)
    logger.log("CYCLE_COMPLETED", cycle=1)
    events = logger.read_all()
    assert [e["event"] for e in events] == [
        "SESSION_START", "CYCLE_START", "CYCLE_COMPLETED"]


def test_monotonic_timestamps(tmp_path):
    logger = GroundTruthLogger(tmp_path / "events.jsonl")
    r1 = logger.log("GRIPPER_CLOSED")
    r2 = logger.log("GRIPPER_OPENED")
    assert r1["timestamp_ms"] <= r2["timestamp_ms"]


def test_rejects_unknown_event(tmp_path):
    logger = GroundTruthLogger(tmp_path / "events.jsonl")
    with pytest.raises(ValueError):
        logger.log("MAGIC_EVENT")
