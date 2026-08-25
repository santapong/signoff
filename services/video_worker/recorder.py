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
        self.writer = cv2.VideoWriter(str(self.output_path), fourcc, fps,
                                      (actual_w, actual_h))
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
        ts_ms = round((time.monotonic() - self._t0) * 1000.0, 1)
        self._index_fh.write(
            json.dumps({"frame": self.frame_count, "ms": ts_ms}) + "\n"
        )
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
            "duration_ms": round(self.frame_count / self.fps * 1000.0, 1),
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
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return json.loads(out.stdout) if out.returncode == 0 else None
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None
