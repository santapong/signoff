"""Frame sources: webcam capture and deterministic video-file replay.

Replay uses frame_index.jsonl (written by the Phase 1 recorder) so detection
runs on exactly the recorded clock shared with ground truth.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np


@dataclass
class Frame:
    frame_no: int
    ms: float  # ms since record/session start (shared clock)
    image: np.ndarray  # BGR uint8


class VideoFileSource:
    """Replay an MP4 with its sibling frame_index.jsonl as the clock."""

    def __init__(self, video_path: Path):
        import cv2  # deferred so tests can use synthetic frames without cv2

        self.video_path = Path(video_path)
        self.cap = cv2.VideoCapture(str(self.video_path))
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open video {self.video_path}")
        index_path = self.video_path.parent / "frame_index.jsonl"
        entries = _load_clock(index_path)
        if entries:
            self.clock = entries
        else:
            # linear fallback on the container's declared fps
            fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
            if fps <= 0:
                fps = 30.0
            def clock(frame_no: int, _fps: float = fps) -> float:
                return round(frame_no / _fps * 1000.0, 1)
            self.clock = clock

    def frames(self) -> Iterator[Frame]:
        n = 0
        while True:
            ok, img = self.cap.read()
            if not ok:
                break
            yield Frame(frame_no=n, ms=self.clock(n), image=img)
            n += 1

    def close(self) -> None:
        self.cap.release()


class SyntheticSource:
    """Deterministic in-memory source for tests: no camera or codec needed."""

    def __init__(self, images: list[np.ndarray], cycle_ms: list[tuple[float, float]],
                 fps: float = 30.0):
        self.images = images
        self.cycle_ms = cycle_ms  # list of (activity_start_ms, activity_end_ms)
        self.fps = fps

    def frames(self) -> Iterator[Frame]:
        total_frames = len(self.images)
        for n, img in enumerate(self.images):
            yield Frame(
                frame_no=n,
                ms=round(n / self.fps * 1000.0, 1),
                image=img,
            )
        assert total_frames >= 0

    def close(self) -> None:  # interface parity
        pass


def _load_clock(index_path: Path) -> dict[int, float]:
    """Frame->ms map from frame_index.jsonl; empty when absent."""
    entries: dict[int, float] = {}
    if index_path.exists():
        with index_path.open() as fh:
            for line in fh:
                rec = json.loads(line)
                entries[int(rec["frame"])] = float(rec["ms"])
    return entries
