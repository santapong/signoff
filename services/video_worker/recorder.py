"""Webcam → MP4 recorder with SHA-256 integrity and frame clock (SDD §19, §20).

Writes alongside the video:
  <name>.meta.json     resolution/fps/duration/sha256
  frame_index.jsonl    frame_no -> ms since record start (shared clock)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

import cv2


class VideoRecorder:
    def __init__(
        self,
        output_path: Path,
        device_index: int = 0,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
    ):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.fps = fps
        self.cap = cv2.VideoCapture(device_index)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open camera {device_index}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.writer = cv2.VideoWriter(str(self.output_path), fourcc, fps, (actual_w, actual_h))
        if not self.writer.isOpened():
            self.cap.release()
            raise RuntimeError("VideoWriter failed to open")
        self.width, self.height = actual_w, actual_h
        self.frame_count = 0
        self._t0 = time.monotonic()
        self._index_fh = (self.output_path.parent / "frame_index.jsonl").open("a")

    def grab(self) -> bool:
        ok, frame = self.cap.read()
        if not ok:
            return False
        self.writer.write(frame)
        ts_ms = (time.monotonic() - self._t0) * 1000.0
        self._last_ms = ts_ms
        self._index_fh.write(json.dumps({"frame": self.frame_count, "ms": ts_ms}) + "\n")
        self.frame_count += 1
        return True

    def run_for(self, seconds: float) -> int:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.grab()
            # pace to requested fps
            target = self._t0 + self.frame_count / self.fps
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        return self.frame_count

    def stop(self) -> dict:
        self.writer.release()
        self.cap.release()
        self._index_fh.close()
        meta = {
            "file": self.output_path.name,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "frames": self.frame_count,
            "duration_ms": getattr(self, "_last_ms", 0.0),
            "clock_basis": "host_receipt_monotonic",
            "qualification": "legacy_lossy_recording",
            "codec": "mp4v",
            "sha256": sha256_file(self.output_path),
        }
        meta_path = self.output_path.with_suffix(".meta.json")
        meta_path.write_text(json.dumps(meta, indent=2))
        return meta


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_with_ffmpeg(path: Path) -> dict | None:
    """Optional richer metadata via ffprobe; None when unavailable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(out.stdout) if out.returncode == 0 else None
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


class RecordingUnavailable(RuntimeError):
    """Archival stopped; prior evidence remains intact."""


class SegmentedRecorder:
    """FFV1/MKV archive sink for frames acquired by the single live owner.

    Never opens a camera. Sidecars preserve the observation clock and decoded
    BGR pixel hash; five-minute file boundaries do not reset the session clock.
    """

    def __init__(
        self,
        output_dir: Path,
        session_id: str = "",
        fps: int = 30,
        segment_ms: float = 300000,
        min_start_bytes: int = 5 * (1 << 30),
        min_remaining_bytes: int = 1 << 30,
        free_space=None,
    ):
        import shutil

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.output_dir / "recording.manifest.json"
        if self.manifest_path.exists():
            raise ValueError("capture archive already exists; use a new session")
        if fps <= 0 or segment_ms <= 0:
            raise ValueError("capture fps and segment duration must be positive")
        self.free_space = free_space or (lambda: shutil.disk_usage(self.output_dir).free)
        if self.free_space() < min_start_bytes:
            raise RecordingUnavailable("capture requires at least 5 GiB free disk")
        self.session_id, self.fps = session_id, fps
        self.segment_ms = segment_ms
        self.min_remaining_bytes = min_remaining_bytes
        self.segments: list[dict] = []
        self.container = None
        self.stream = None
        self.clock_fh = None
        self.segment_frames = 0
        self.segment_start_ms = 0.0
        self.last_ms: float | None = None
        self.session_end_ms: float | None = None
        self.last_frame_no: int | None = None
        self.dimensions: tuple[int, int] | None = None
        self.status = "recording"
        self.unavailable_since_ms: float | None = None
        self.error: str | None = None
        self._write_manifest()

    def _write_manifest(self) -> None:
        meta = {
            "schema_version": 2,
            "session_id": self.session_id,
            "status": self.status,
            "codec": "ffv1",
            "pixel_format": "bgr0",
            "segments": self.segments,
            "end_ms": self.session_end_ms if self.session_end_ms is not None else self.last_ms,
            "unavailable_since_ms": self.unavailable_since_ms,
            "error": self.error,
            "fps_requested": self.fps,
            "dimensions": list(self.dimensions) if self.dimensions else None,
        }
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(meta, indent=2, sort_keys=True))
        temporary.replace(self.manifest_path)

    def _open_segment(self, frame) -> None:
        from fractions import Fraction

        import av

        h, w = frame.image.shape[:2]
        segment_number = len(self.segments)
        self.segment_id = f"{segment_number:05d}"
        self.segment_path = self.output_dir / f"recording-{self.segment_id}.mkv"
        self.clock_path = self.segment_path.with_suffix(".clock.jsonl")
        if self.segment_path.exists() or self.clock_path.exists():
            raise RecordingUnavailable("refusing to overwrite capture evidence")
        self.container = av.open(str(self.segment_path), mode="w", format="matroska")
        self.stream = self.container.add_stream("ffv1", rate=self.fps)
        self.stream.width, self.stream.height = w, h
        self.stream.pix_fmt = "bgr0"
        self.stream.time_base = Fraction(1, 1000)
        self.stream.codec_context.time_base = Fraction(1, 1000)
        self.clock_fh = self.clock_path.open("x")
        self.segment_frames = 0
        self.segment_start_ms = frame.ms

    def _close_segment(self) -> None:
        import os

        if self.container is None:
            return
        try:
            for packet in self.stream.encode():
                self.container.mux(packet)
        finally:
            self.container.close()
            self.container = None
            self.clock_fh.flush()
            os.fsync(self.clock_fh.fileno())
            self.clock_fh.close()
            self.clock_fh = None
        self.segments.append(
            {
                "segment_id": self.segment_id,
                "file": self.segment_path.name,
                "clock_file": self.clock_path.name,
                "frames": self.segment_frames,
                "start_ms": self.segment_start_ms,
                "end_ms": self.last_ms,
                "sha256": sha256_file(self.segment_path),
                "clock_sha256": sha256_file(self.clock_path),
            }
        )
        self._write_manifest()

    def consume(self, frame) -> None:
        from fractions import Fraction

        import av

        from packages.cv.signoff_cv.source import pixel_hash

        if self.status != "recording":
            raise RecordingUnavailable(self.error or "capture archive is stopped")
        try:
            if self.free_space() < self.min_remaining_bytes:
                raise RecordingUnavailable("capture stopped before free disk fell below 1 GiB")
            if self.last_ms is not None and (
                frame.ms <= self.last_ms or frame.frame_no <= self.last_frame_no
            ):
                raise ValueError("capture observations must be ordered")
            dimensions = (frame.image.shape[1], frame.image.shape[0])
            if self.dimensions is not None and dimensions != self.dimensions:
                raise RecordingUnavailable("resolution changed during capture")
            self.dimensions = dimensions
            if self.container is not None and frame.ms - self.segment_start_ms >= self.segment_ms:
                self._close_segment()
            if self.container is None:
                self._open_segment(frame)
            encoded = av.VideoFrame.from_ndarray(frame.image, format="bgr24")
            encoded.pts = int(round(frame.ms - self.segment_start_ms))
            encoded.time_base = Fraction(1, 1000)
            for packet in self.stream.encode(encoded):
                self.container.mux(packet)
            row = {
                "schema_version": 2,
                "session_id": self.session_id or frame.session_id,
                "segment_id": self.segment_id,
                "segment_frame": self.segment_frames,
                "frame": frame.frame_no,
                "ms": frame.ms,
                "clock_basis": frame.clock_basis,
                "receipt_ms": frame.receipt_ms,
                "quality_flags": list(frame.quality_flags),
                "pixel_sha256": pixel_hash(frame.image),
            }
            self.clock_fh.write(json.dumps(row, sort_keys=True) + "\n")
            self.clock_fh.flush()
            self.segment_frames += 1
            self.last_ms, self.last_frame_no = frame.ms, frame.frame_no
        except Exception as exc:
            self.status = "interrupted"
            self.error = str(exc)
            self.unavailable_since_ms = frame.ms
            self.session_end_ms = frame.ms
            try:
                self._close_segment()
            finally:
                self._write_manifest()
            if isinstance(exc, RecordingUnavailable):
                raise
            raise RecordingUnavailable(f"capture archive failed: {exc}") from exc

    def stop(self, end_ms: float | None = None) -> dict:
        if end_ms is not None:
            if end_ms < (self.last_ms or 0):
                raise ValueError("capture end cannot precede its last observation")
            self.session_end_ms = end_ms
        if self.container is not None:
            self._close_segment()
        if self.status == "recording":
            self.status = "complete"
        self._write_manifest()
        return json.loads(self.manifest_path.read_text())
