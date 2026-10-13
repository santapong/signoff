"""Timestamped observations and replay on capture clocks or decoded PTS."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Frame:
    frame_no: int
    ms: float
    image: np.ndarray
    session_id: str = ""
    segment_id: str = ""
    clock_basis: str = "unknown"
    receipt_ms: float | None = None
    quality_flags: tuple[str, ...] = ()


def pixel_hash(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def _read_clock(index_path: Path) -> list[dict]:
    if not index_path.exists():
        return []
    rows = []
    previous_ms = -1.0
    with index_path.open() as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            number = int(rec.get("segment_frame", rec["frame"]))
            ms = float(rec["ms"])
            if number != len(rows) or not math.isfinite(ms) or ms < 0:
                raise ValueError("frame clock must be contiguous with finite nonnegative times")
            if ms <= previous_ms:
                raise ValueError("frame clock must be strictly increasing")
            rows.append(rec)
            previous_ms = ms
    if not rows:
        raise ValueError("frame clock is empty")
    return rows


class VideoFileSource:
    """Decode once with PyAV. Sidecars are authoritative and strictly validated."""

    def __init__(self, video_path: Path, clock_path: Path | None = None):
        import av

        self.video_path = Path(video_path)
        if clock_path is None:
            candidate = self.video_path.with_suffix(".clock.jsonl")
            legacy = self.video_path.parent / "frame_index.jsonl"
            clock_path = candidate if candidate.exists() else legacy if legacy.exists() else None
        if clock_path is not None and not Path(clock_path).is_file():
            raise ValueError("requested frame clock is missing")
        self.clock_path = Path(clock_path) if clock_path is not None else None
        self.clock_rows = _read_clock(self.clock_path) if self.clock_path else []
        self.clock = {i: float(row["ms"]) for i, row in enumerate(self.clock_rows)}
        self.container = av.open(str(self.video_path))
        if not self.container.streams.video:
            self.container.close()
            raise ValueError("media contains no video stream")
        self.stream = self.container.streams.video[0]
        self.clock_basis = "capture_sidecar" if self.clock_rows else "decoded_pts"
        self.duration_ms: float | None = None
        if not self.clock_rows and self.stream.duration is not None:
            self.duration_ms = float(self.stream.duration * self.stream.time_base) * 1000.0

    def frames(self) -> Iterator[Frame]:
        previous_ms = -1.0
        origin_pts: float | None = None
        count = 0
        for n, decoded in enumerate(self.container.decode(self.stream)):
            image = decoded.to_ndarray(format="bgr24")
            if self.clock_rows:
                if n >= len(self.clock_rows):
                    raise ValueError("frame clock does not cover all decoded frames")
                row = self.clock_rows[n]
                ms = float(row["ms"])
                if row.get("pixel_sha256") and pixel_hash(image) != row["pixel_sha256"]:
                    raise ValueError("decoded pixels do not match capture evidence hash")
            else:
                if decoded.pts is None or decoded.time_base is None:
                    raise ValueError("video has no trustworthy presentation timestamps")
                pts_ms = float(decoded.pts * decoded.time_base) * 1000.0
                if origin_pts is None:
                    origin_pts = pts_ms
                ms = pts_ms - origin_pts
                row = {}
            if not math.isfinite(ms) or ms < 0 or ms <= previous_ms:
                raise ValueError("video timestamps must be finite and strictly increasing")
            previous_ms = ms
            count += 1
            yield Frame(
                int(row.get("frame", n)),
                ms,
                image,
                str(row.get("session_id", "")),
                str(row.get("segment_id", "")),
                str(row.get("clock_basis", self.clock_basis)),
                row.get("receipt_ms"),
                tuple(row.get("quality_flags", [])),
            )
        if self.clock_rows and count != len(self.clock_rows):
            raise ValueError("frame clock contains records without decoded frames")
        if count == 0:
            raise ValueError("video contains no decodable frames")

    def close(self) -> None:
        self.container.close()


class SyntheticSource:
    """Deterministic in-memory source; always labelled synthetic."""

    def __init__(
        self, images: list[np.ndarray], cycle_ms: list[tuple[float, float]], fps: float = 30.0
    ):
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("fps must be positive")
        self.images, self.cycle_ms, self.fps = images, cycle_ms, fps

    def frames(self) -> Iterator[Frame]:
        for n, img in enumerate(self.images):
            yield Frame(n, round(n / self.fps * 1000.0, 1), img, clock_basis="synthetic")

    def close(self) -> None:
        pass


def _load_clock(index_path: Path) -> dict[int, float]:
    """Legacy helper, now with strict coverage/order validation."""
    return {int(row["frame"]): float(row["ms"]) for row in _read_clock(index_path)}
