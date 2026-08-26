"""Deterministic mock arm: lets the whole rig run and be tested without hardware."""
from __future__ import annotations

import time

from robotics.elexhub.controller.base import RobotArm


class MockArm(RobotArm):
    joint_limits_deg = {f"joint_{i}": (-120.0, 120.0) for i in range(1, 7)}
    max_speed_deg_per_s = 90.0

    def __init__(self, step_delay: float = 0.0):
        self.step_delay = step_delay
        self.positions = {f"joint_{i}": 0.0 for i in range(1, 7)}
        self.gripper_closed = False
        self.move_log: list[tuple[str, float, float]] = []

    def home(self) -> None:
        self.positions = {j: 0.0 for j in self.positions}
        if self.step_delay:
            time.sleep(self.step_delay)

    def move_joint(self, joint: str, angle: float, duration: float) -> float:
        self.validate_joint(joint, angle)
        effective = self.validate_duration(duration)
        self.positions[joint] = angle
        self.move_log.append((joint, angle, effective))
        if self.step_delay:
            time.sleep(self.step_delay)
        return effective

    def move_pose(self, joint_targets: dict[str, float], duration: float) -> float:
        effective = duration
        for joint, angle in joint_targets.items():
            effective = self.move_joint(joint, angle, duration)
        return effective

    def open_gripper(self) -> None:
        self.gripper_closed = False

    def close_gripper(self) -> None:
        self.gripper_closed = True

    def emergency_stop(self) -> None:
        self.step_delay = 0.0
