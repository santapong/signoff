"""Camera metadata and preview diagnostics; discovery never opens a device."""

from __future__ import annotations

import math
import os
import re
import shutil
from pathlib import Path


def discover_cameras(
    sys_root: Path = Path("/sys/class/video4linux"), dev_root: Path = Path("/dev")
) -> list[dict]:
    # Use nodes exposed to this process, not host-only sysfs nodes in a container.
    nodes = list(dev_root.glob("video*")) if dev_root.is_dir() else []
    cameras = []
    for node in nodes:
        matched = re.fullmatch(r"video(\d+)", node.name)
        if not matched or int(matched[1]) > 255:
            continue
        try:
            metadata = sys_root / node.name
            device_number = node.stat().st_rdev
            if device_number:
                alias = Path(f"/sys/dev/char/{os.major(device_number)}:{os.minor(device_number)}")
                if alias.is_dir():
                    metadata = alias
            name = (metadata / "name").read_text().strip()
        except OSError:
            name = ""
        stable_paths = []
        for folder in ("by-id", "by-path"):
            aliases = dev_root / "v4l" / folder
            if aliases.is_dir():
                for alias in aliases.iterdir():
                    if alias.is_symlink() and alias.resolve() == node.resolve():
                        stable_paths.append(str(alias))
        cameras.append(
            {
                "device_index": int(matched[1]),
                "name": name or node.name,
                "path": str(dev_root / node.name),
                "capture_capability": "unverified",
                "stable_paths": sorted(stable_paths),
            }
        )
    return sorted(cameras, key=lambda item: item["device_index"])


def disk_headroom(path: Path) -> int | None:
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


class MarkerReadiness:
    def __init__(self):
        import cv2

        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self.detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
        self.detected: dict[int, list] = {}
        self.updated_ms: float | None = None

    def consume(self, image, now_ms: float) -> None:
        if self.updated_ms is not None and now_ms - self.updated_ms < 200:
            return
        corners, ids, _ = self.detector.detectMarkers(image)
        h, w = image.shape[:2]
        self.detected = (
            {}
            if ids is None
            else {
                int(marker_id): [[float(x / w), float(y / h)] for x, y in points.reshape(4, 2)]
                for marker_id, points in zip(ids.flatten(), corners)
            }
        )
        self.updated_ms = now_ms

    def snapshot(self, profile: dict | None) -> dict:
        detector = (profile or {}).get("detector", {})
        reference, tool = detector.get("reference_marker_id", 0), detector.get("tool_marker_id", 1)
        return {
            "reference_id": reference,
            "tool_id": tool,
            "reference_visible": reference in self.detected,
            "tool_visible": tool in self.detected,
            "detected_ids": sorted(self.detected),
            "corners": {str(key): value for key, value in self.detected.items()},
        }


class DeadlinePacer:
    """Account for read/processing time instead of adding a full extra period."""

    def __init__(self, period_s: float, clock):
        if not math.isfinite(period_s) or period_s <= 0:
            raise ValueError("capture polling interval must be positive")
        self.period_s, self.clock = period_s, clock
        self.deadline = clock()

    def remaining(self) -> float:
        self.deadline += self.period_s
        now = self.clock()
        delay = max(0.0, self.deadline - now)
        if now > self.deadline:
            self.deadline = now
        return delay

    def reset(self) -> None:
        self.deadline = self.clock()
