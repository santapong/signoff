"""Scripted reference pick-transfer-place cycle runner (SDD §9, §38, §40).

Drives an abstract RobotArm through the reference cycle and writes hidden
ground-truth events. Supports fault injection for E7 dataset construction.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

from robotics.elexhub.controller.base import RobotArm
from robotics.elexhub.logging.ground_truth import GroundTruthLogger

FaultMode = Literal["none", "grasp_miss", "drop", "interrupt"]

# joint waypoints for the reference cycle (mock-scale angles)
WAYPOINTS: dict[str, dict[str, float]] = {
    "APPROACH_SOURCE": {"joint_1": -30.0, "joint_2": 20.0, "joint_3": 45.0},
    "LOWER": {"joint_3": 70.0},
    "LIFT": {"joint_3": 20.0},
    "MOVE_DESTINATION": {"joint_1": 30.0, "joint_2": 20.0},
    "RETRACT": {"joint_2": 0.0},
}


@dataclass
class CycleResult:
    cycle: int
    outcome: str
    fault: FaultMode


class ExperimentRunner:
    def __init__(self, arm: RobotArm, logger: GroundTruthLogger, seed: int | None = None):
        self.arm = arm
        self.logger = logger
        self.rng = random.Random(seed)

    def run_cycles(self, n: int, fault_every: int = 0) -> list[CycleResult]:
        results = []
        for i in range(1, n + 1):
            fault: FaultMode = (
                self.rng.choice(["grasp_miss", "drop", "interrupt"])
                if fault_every and i % fault_every == 0
                else "none"
            )
            results.append(self.run_cycle(i, fault))
        return results

    def run_cycle(self, index: int, fault: FaultMode = "none") -> CycleResult:
        arm = self.arm
        try:
            arm.home()
            self.logger.log("CYCLE_START", cycle=index)

            arm.move_pose(WAYPOINTS["APPROACH_SOURCE"], duration=1.0)
            arm.move_pose(WAYPOINTS["LOWER"], duration=0.6)

            if fault == "grasp_miss":
                # gripper closes on empty air; object stays behind
                arm.close_gripper()
                self.logger.log("GRIPPER_CLOSED", cycle=index)
                self.logger.log("FAULT_INJECTED", cycle=index, fault=fault)
            else:
                arm.close_gripper()
                self.logger.log("GRIPPER_CLOSED", cycle=index)
                self.logger.log("OBJECT_ACQUIRED", cycle=index)

            arm.move_pose(WAYPOINTS["LIFT"], duration=0.8)

            if fault == "drop":
                self.logger.log("OBJECT_DROPPED", cycle=index, phase="lift")
                self.logger.log("FAULT_INJECTED", cycle=index, fault=fault)

            arm.move_pose(WAYPOINTS["MOVE_DESTINATION"], duration=1.4)
            arm.move_pose(WAYPOINTS["LOWER"], duration=0.6)
            arm.open_gripper()
            self.logger.log("GRIPPER_OPENED", cycle=index)
            arm.move_pose(WAYPOINTS["RETRACT"], duration=0.5)

            if fault == "interrupt":
                raise KeyboardInterrupt("injected interrupt")

            arm.home()
            self.logger.log("CYCLE_COMPLETED", cycle=index, fault=fault)
            return CycleResult(index, "OK" if fault == "none" else "FAILED", fault)
        except (KeyboardInterrupt, RuntimeError):
            self.logger.log("FAULT_INJECTED", cycle=index, fault="interrupt")
            arm.emergency_stop()
            return CycleResult(index, "FAILED", "interrupt")
