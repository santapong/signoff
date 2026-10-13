"""Visible HOME→PICK→PLACE→HOME completion; no object-success inference."""

from __future__ import annotations

import hashlib

import numpy as np

from packages.cv.signoff_cv.source import Frame


def event_id(profile_hash: str, frame: Frame, kind: str) -> str:
    key = f"{profile_hash}:{frame.frame_no}:{frame.ms:.6f}:{kind}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


class MarkerTaskDetector:
    def __init__(self, profile: dict):
        import cv2

        self.profile = profile
        self.config = profile["detector"]
        self.profile_hash = profile.get("profile_hash", "unversioned")
        dictionary_name = self.config.get("dictionary", "DICT_4X4_50")
        if not hasattr(cv2.aruco, dictionary_name):
            raise ValueError("unsupported ArUco dictionary")
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary_name))
        self.detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
        self.reference: np.ndarray | None = None
        self.displaced_frames = 0
        self.invalidated = False
        self.last_valid_ms: float | None = None
        self.region: str | None = None
        self.region_first: Frame | None = None
        self.region_count = 0
        self.armed = False
        self.current: Frame | None = None
        self.phase = "UNARMED"
        self.completed = 0

    def _partial(self, frame: Frame, reason: str) -> list[dict]:
        events = []
        if self.current is not None:
            events.append(self._cycle(self.current, frame, "PARTIAL", reason))
        self.current = None
        self.armed = False
        self.phase = "UNARMED"
        self.region = None
        self.region_first = None
        self.region_count = 0
        return events

    def _cycle(self, start: Frame, end: Frame, status: str, reason: str = "") -> dict:
        return {
            "type": "cycle_completed" if status == "COMPLETE" else "cycle_partial",
            "cycle_id": event_id(self.profile_hash, start, "cycle"),
            "cycle": self.completed,
            "start_ms": start.ms,
            "end_ms": end.ms,
            "start_frame": start.frame_no,
            "end_frame": end.frame_no,
            "session_id": start.session_id,
            "status": status,
            "basis": "VISIBLE_SEQUENCE",
            "outcome": "UNKNOWN",
            "reason": reason,
        }

    def gap(self, frame: Frame, reason: str) -> list[dict]:
        return self._partial(frame, reason)

    def process(self, frame: Frame) -> tuple[list[dict], str, list[str]]:
        corners, ids, _ = self.detector.detectMarkers(frame.image)
        markers = (
            {}
            if ids is None
            else {
                int(marker_id): points.reshape(4, 2)
                for marker_id, points in zip(ids.flatten(), corners)
            }
        )
        return self.observe(frame, markers)

    def observe(
        self, frame: Frame, markers: dict[int, np.ndarray]
    ) -> tuple[list[dict], str, list[str]]:
        """Marker extraction seam also permits deterministic protocol tests."""
        cfg = self.config
        h, w = frame.image.shape[:2]
        camera = self.profile.get("camera", {})
        if (w, h) != (camera.get("width", w), camera.get("height", h)):
            self.invalidated = True
        if self.invalidated:
            return self._partial(frame, "PROFILE_INVALIDATED"), "UNKNOWN", ["PROFILE_INVALIDATED"]
        reference = markers.get(cfg.get("reference_marker_id", 0))
        tool = markers.get(cfg.get("tool_marker_id", 1))
        if reference is None or tool is None:
            self.region = None
            self.region_first = None
            self.region_count = 0
            events = []
            if self.last_valid_ms is not None and frame.ms - self.last_valid_ms > cfg.get(
                "visibility_gap_ms", 250
            ):
                events = self._partial(frame, "MARKER_VISIBILITY_GAP")
            return events, "UNKNOWN", ["MARKER_NOT_VISIBLE"]
        if self.reference is None:
            self.reference = reference.copy()
        displacement = float(np.max(np.linalg.norm(reference - self.reference, axis=1)))
        self.displaced_frames = (
            self.displaced_frames + 1 if displacement > 0.02 * np.hypot(w, h) else 0
        )
        if self.displaced_frames >= 5:
            self.invalidated = True
            return (
                self._partial(frame, "CAMERA_MOVED"),
                "UNKNOWN",
                ["CAMERA_MOVED", "PROFILE_INVALIDATED"],
            )
        events = []
        if self.last_valid_ms is not None and frame.ms - self.last_valid_ms > cfg.get(
            "visibility_gap_ms", 250
        ):
            events += self._partial(frame, "MARKER_VISIBILITY_GAP")
        self.last_valid_ms = frame.ms
        if self.current is not None and frame.ms - self.current.ms > cfg.get("max_cycle_ms", 60000):
            events += self._partial(frame, "CYCLE_TIMEOUT")
        x, y = tool.mean(axis=0) / np.array([w, h])
        region = "OUTSIDE"
        for name in ("home", "pick", "place"):
            rx, ry, rw, rh = cfg[name]
            if rx <= x < rx + rw and ry <= y < ry + rh:
                region = name.upper()
                break
        if region != self.region:
            self.region = region
            self.region_first = frame
            self.region_count = 1
        else:
            self.region_count += 1
        assert self.region_first is not None
        dwell = frame.ms - self.region_first.ms
        home_stable = (
            region == "HOME" and dwell >= cfg.get("home_dwell_ms", 200) and self.region_count >= 2
        )
        visit_stable = dwell >= cfg.get("visit_dwell_ms", 100) and self.region_count >= 2
        if self.current is None:
            if home_stable:
                self.armed = True
                self.phase = "HOME"
            elif self.armed and region != "HOME":
                self.current = frame
                self.phase = "TO_PICK"
                events.append(
                    {
                        "type": "cycle_started",
                        "cycle_id": event_id(self.profile_hash, frame, "cycle"),
                        "start_ms": frame.ms,
                        "start_frame": frame.frame_no,
                    }
                )
        if self.current is not None:
            if region == "HOME" and home_stable:
                if self.phase == "TO_HOME":
                    self.completed += 1
                    events.append(self._cycle(self.current, self.region_first, "COMPLETE"))
                    self.current = None
                    self.armed = True
                    self.phase = "HOME"
                else:
                    events += self._partial(frame, "REQUIRED_REGION_SKIPPED")
            elif visit_stable and region == "PICK" and self.phase == "TO_PICK":
                self.phase = "TO_PLACE"
            elif visit_stable and region == "PLACE":
                if self.phase == "TO_PLACE":
                    self.phase = "TO_HOME"
                elif self.phase == "TO_PICK":
                    events += self._partial(frame, "REQUIRED_REGION_SKIPPED")
        return events, "IDLE" if region == "HOME" else "ACTIVE", []

    def finish(self, frame: Frame) -> list[dict]:
        return self._partial(frame, "END_OF_RECORDING")


class MotionCycleDetector:
    """Streaming experimental baseline; finalize only after a quiet merge window."""

    def __init__(
        self,
        profile_hash: str = "motion-baseline",
        enter_score: float = 4,
        exit_score: float = 2,
        min_active_ms: float = 400,
        refractory_ms: float = 500,
        merge_gap_ms: float = 300,
    ):
        self.profile_hash = profile_hash
        self.enter_score, self.exit_score = enter_score, exit_score
        self.min_active_ms, self.refractory_ms, self.merge_gap_ms = (
            min_active_ms,
            refractory_ms,
            merge_gap_ms,
        )
        self.current: Frame | None = None
        self.pending_end: Frame | None = None
        self.last_end = -float("inf")
        self.completed = 0

    def _event(self, end: Frame, status: str, reason: str = "") -> dict:
        assert self.current is not None
        return {
            "type": "cycle_completed" if status == "COMPLETE" else "cycle_partial",
            "cycle_id": event_id(self.profile_hash, self.current, "cycle"),
            "cycle": self.completed,
            "start_ms": self.current.ms,
            "end_ms": end.ms,
            "start_frame": self.current.frame_no,
            "end_frame": end.frame_no,
            "session_id": self.current.session_id,
            "status": status,
            "basis": "MOTION_BASELINE",
            "outcome": "UNKNOWN",
            "reason": reason,
        }

    def process(self, frame: Frame, score: float) -> tuple[list[dict], str, list[str]]:
        events = []
        if (
            self.current is None
            and score >= self.enter_score
            and frame.ms - self.last_end >= self.refractory_ms
        ):
            self.current = frame
        if self.current is not None:
            if score >= self.exit_score:
                self.pending_end = None
            elif self.pending_end is None:
                self.pending_end = frame
            elif frame.ms - self.pending_end.ms >= self.merge_gap_ms:
                if self.pending_end.ms - self.current.ms >= self.min_active_ms:
                    self.completed += 1
                    events.append(self._event(self.pending_end, "COMPLETE"))
                self.last_end = self.pending_end.ms
                self.current = None
                self.pending_end = None
        return events, "ACTIVE" if score >= self.exit_score else "IDLE", []

    def gap(self, frame: Frame, reason: str) -> list[dict]:
        events = [self._event(frame, "PARTIAL", reason)] if self.current else []
        self.current = None
        self.pending_end = None
        self.last_end = frame.ms
        return events

    def finish(self, frame: Frame) -> list[dict]:
        return self.gap(frame, "END_OF_RECORDING")
