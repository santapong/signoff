"""Serial driver skeleton — refuses to run until hardware discovery gate passes.

SDD §7: no software-defined servo limit is safe until physically verified.
Fill in `hardware_profile.yaml`; any UNKNOWN field blocks operation.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from robotics.elexhub.controller.base import RobotArm, SafetyViolation

UNKNOWN = "UNKNOWN"


def load_hardware_profile(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


def hardware_gate(profile: dict) -> list[str]:
    """Return the list of fields still UNKNOWN (empty = gate passed)."""
    unknown: list[str] = []

    def walk(node, prefix=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{prefix}{k}.")
        elif node == UNKNOWN or node is None:
            unknown.append(prefix.rstrip("."))

    walk(profile)
    return unknown


class SerialArm(RobotArm):
    def __init__(self, profile_path: Path):
        profile = load_hardware_profile(profile_path)
        remaining = hardware_gate(profile)
        if remaining:
            raise SafetyViolation(
                "hardware discovery gate failed; UNKNOWN fields: "
                + ", ".join(remaining)
            )
        self.profile = profile
        # Joint limits come from the *verified* profile, never invented.
        self.joint_limits_deg = {
            name: tuple(limits)
            for name, limits in profile.get("verified_joint_limits_deg", {}).items()
        }
        self.max_speed_deg_per_s = float(
            profile.get("verified_max_speed_deg_per_s", 30.0)
        )
        self._conn = None  # real serial transport intentionally not implemented yet

    # -- interface implementation deferred until controller identified --
    def home(self) -> None:
        raise NotImplementedError("serial transport not implemented")

    def move_joint(self, joint, angle, duration) -> float:
        raise NotImplementedError("serial transport not implemented")

    def move_pose(self, joint_targets, duration) -> float:
        raise NotImplementedError("serial transport not implemented")

    def open_gripper(self) -> None:
        raise NotImplementedError("serial transport not implemented")

    def close_gripper(self) -> None:
        raise NotImplementedError("serial transport not implemented")

    def emergency_stop(self) -> None:
        raise NotImplementedError("serial transport not implemented")
