import pytest

from robotics.elexhub.controller.base import SafetyViolation
from robotics.elexhub.controller.mock_arm import MockArm
from robotics.elexhub.controller.serial_arm import SerialArm, hardware_gate
from robotics.elexhub.logging.ground_truth import GroundTruthLogger
from robotics.elexhub.trajectories.runner import ExperimentRunner


def test_joint_limit_enforced():
    arm = MockArm()
    with pytest.raises(SafetyViolation):
        arm.move_joint("joint_1", 999.0, duration=1.0)


def test_speed_cap_enforced():
    arm = MockArm()
    effective = arm.move_joint("joint_1", 90.0, duration=0.00001)
    assert effective >= 1.0 / arm.max_speed_deg_per_s


def test_runner_n_complete_cycles(tmp_path):
    arm = MockArm()
    runner = ExperimentRunner(arm, GroundTruthLogger(tmp_path / "gt.jsonl"), seed=1)
    results = runner.run_cycles(10)
    assert len(results) == 10
    assert all(r.outcome == "OK" for r in results)


def test_runner_ground_truth_pairs(tmp_path):
    arm = MockArm()
    logger = GroundTruthLogger(tmp_path / "gt.jsonl")
    ExperimentRunner(arm, logger, seed=2).run_cycles(5)
    events = logger.read_all()
    starts = sum(1 for e in events if e["event"] == "CYCLE_START")
    ends = sum(1 for e in events if e["event"] == "CYCLE_COMPLETED")
    assert starts == 5 and ends == 5


def test_runner_fault_injection(tmp_path):
    arm = MockArm()
    logger = GroundTruthLogger(tmp_path / "gt.jsonl")
    results = ExperimentRunner(arm, logger, seed=3).run_cycles(
        6, fault_every=3)  # cycles 3 and 6 faulted
    failed = [r for r in results if r.outcome == "FAILED"]
    assert [r.cycle for r in failed] == [3, 6]
    faults = [e for e in logger.read_all() if e["event"] == "FAULT_INJECTED"]
    assert len(faults) == 2


def test_hardware_gate_blocks_unknown(tmp_path):
    profile = {
        "robot": {"manufacturer": "ELEXHUB", "dof": 6},
        "controller": {"type": "UNKNOWN"},
        "verified_joint_limits_deg": {},
    }
    unknown = hardware_gate(profile)
    assert "controller.type" in unknown
    bad = tmp_path / "profile.yaml"
    bad.write_text("controller:\n  type: UNKNOWN\n")
    with pytest.raises(SafetyViolation):
        SerialArm(bad)
