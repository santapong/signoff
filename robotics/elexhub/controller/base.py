"""Abstract robot-arm driver interface (SDD §39) with safety hooks (SAF-002/003)."""
from __future__ import annotations

from abc import ABC, abstractmethod


class SafetyViolation(RuntimeError):
    pass


class RobotArm(ABC):
    """Hardware-specific communication lives behind this interface."""

    joint_limits_deg: dict[str, tuple[float, float]] = {}
    max_speed_deg_per_s: float = 60.0

    def validate_joint(self, joint: str, angle: float) -> None:
        if joint in self.joint_limits_deg:
            lo, hi = self.joint_limits_deg[joint]
            if not lo <= angle <= hi:
                raise SafetyViolation(
                    f"joint {joint}={angle} outside safe range [{lo}, {hi}]"
                )

    def validate_duration(self, duration: float) -> float:
        """Return the effective duration enforcing the speed cap (SAF-003)."""
        min_duration = 1.0 / self.max_speed_deg_per_s
        return max(duration, min_duration)

    @abstractmethod
    def home(self) -> None: ...

    @abstractmethod
    def move_joint(self, joint: str, angle: float, duration: float) -> float: ...

    @abstractmethod
    def move_pose(self, joint_targets: dict[str, float], duration: float) -> float: ...

    @abstractmethod
    def open_gripper(self) -> None: ...

    @abstractmethod
    def close_gripper(self) -> None: ...

    @abstractmethod
    def emergency_stop(self) -> None: ...
