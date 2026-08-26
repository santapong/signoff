"""Ground-truth event logger — HIDDEN from the vision pipeline (SDD §40).

Writes JSONL with monotonic ms since logger epoch + wall-clock ISO. The file
this produces must never be an input to inference during evaluation; it exists
only for post-hoc comparison of vision predictions against true robot events.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock

EVENT_TYPES = {
    "CYCLE_START",
    "CYCLE_COMPLETED",
    "GRIPPER_CLOSED",
    "GRIPPER_OPENED",
    "OBJECT_ACQUIRED",
    "OBJECT_DROPPED",
    "FAULT_INJECTED",
    "SESSION_START",
    "SESSION_END",
}


class GroundTruthLogger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._t0 = time.monotonic()
        self._lock = Lock()
        self.log("SESSION_START")

    def now_ms(self) -> float:
        return round((time.monotonic() - self._t0) * 1000.0, 1)

    def log(self, event: str, **fields) -> dict:
        if event not in EVENT_TYPES:
            raise ValueError(f"unknown event type {event!r}")
        record = {
            "timestamp_ms": self.now_ms(),
            "wall_clock": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            + f".{int(time.time() * 1000) % 1000:03d}Z",
            "event": event,
            **fields,
        }
        line = json.dumps(record, sort_keys=True)
        with self._lock:
            with self.path.open("a") as fh:
                fh.write(line + "\n")
        return record

    def read_all(self) -> list[dict]:
        """Evaluation-time accessor only. Never call during inference."""
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]
