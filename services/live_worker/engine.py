"""Single-owner capture → archive + shared measurement → durable live events."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from packages.cv.signoff_cv.pipeline import MeasurementPipeline
from packages.cv.signoff_cv.source import Frame
from services.video_worker.recorder import RecordingUnavailable, SegmentedRecorder

LATENCY_BUDGET_MS = 5000.0


@dataclass
class LiveEvent:
    type: str
    capture_ms: float
    emit_ms: float
    payload: dict = field(default_factory=dict)

    @property
    def latency_ms(self) -> float:
        return self.emit_ms - self.capture_ms

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "capture_ms": self.capture_ms,
            "emit_ms": self.emit_ms,
            "latency_ms": round(self.latency_ms, 1),
            "within_budget": self.latency_ms < LATENCY_BUDGET_MS,
            **self.payload,
        }


class EventBus:
    """Bounded event history; subscribers must use nonblocking queue writes."""

    def __init__(self, maxlen: int = 500):
        self._events: deque[LiveEvent] = deque(maxlen=maxlen)
        self._subscribers: list[Callable[[LiveEvent], None]] = []
        self._lock = threading.Lock()

    def publish(self, event: LiveEvent) -> None:
        event.emit_ms = time.monotonic() * 1000.0
        with self._lock:
            self._events.append(event)
            subscribers = list(self._subscribers)
        for sub in subscribers:
            try:
                sub(event)
            except Exception:
                pass

    def subscribe(self, fn: Callable[[LiveEvent], None]) -> None:
        with self._lock:
            if len(self._subscribers) >= 100:
                raise RuntimeError("too many event subscribers")
            self._subscribers.append(fn)

    def unsubscribe(self, fn: Callable[[LiveEvent], None]) -> None:
        with self._lock:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

    def recent(self, n: int = 50) -> list[LiveEvent]:
        with self._lock:
            return list(self._events)[-n:]


class FrameSourceProtocol:
    def read(self):
        raise NotImplementedError

    def close(self) -> None:
        pass


class SyntheticLiveSource:
    def __init__(
        self,
        activity_windows_ms: list[tuple[float, float]],
        fps: float = 30,
        width: int = 160,
        height: int = 120,
    ):
        import numpy as np

        self.windows, self.fps = activity_windows_ms, fps
        self.t0 = time.monotonic() * 1000
        self.width, self.height = width, height
        self.fail_until: float | None = None
        self.np = np
        self.synthetic = True

    def _active_now(self, ms: float) -> bool:
        return any(start <= ((ms - self.t0) % 8000) <= end for start, end in self.windows)

    def read(self):
        now = time.monotonic() * 1000
        if self.fail_until is not None and now < self.fail_until:
            return False, None
        img = self.np.zeros((self.height, self.width, 3), dtype=self.np.uint8)
        if self._active_now(now):
            sx, sy = self.width / 160, self.height / 120
            jx, jy = int((int(now / 30) % 15) * sx), int((int(now / 30) % 20) * sy)
            img[int(40 * sy) + jy : int(80 * sy) + jy, int(60 * sx) - jx : int(100 * sx) - jx] = (
                255,
            ) * 3
        return True, img

    def close(self) -> None:
        pass


class OpenCvLiveSource:
    def __init__(self, device_index: int = 0, width: int = 1280, height: int = 720, fps: int = 30):
        import cv2

        if isinstance(device_index, bool) or device_index < 0:
            raise ValueError("camera device must be a nonnegative integer")
        self.cap = cv2.VideoCapture(device_index)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(f"cannot open camera {device_index}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.negotiated = {
            "width": int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": self.cap.get(cv2.CAP_PROP_FPS),
        }
        self.synthetic = False

    def read(self):
        ok, image = self.cap.read()
        return ok, image if ok else None

    def close(self) -> None:
        self.cap.release()


class LiveEngine:
    """Cumulative stable completions; source is acquired once and archived once."""

    def __init__(
        self,
        source,
        bus: EventBus,
        window_s: float = 12,
        enter_score: float = 4,
        exit_score: float = 2,
        min_active_ms: float = 400,
        refractory_ms: float = 500,
        merge_gap_ms: float = 300,
        max_lost_grabs: int = 10,
        poll_interval_s: float = 1 / 30,
        profile: dict | None = None,
        session_dir: Path | None = None,
        session_id: str | None = None,
    ):
        self.source, self.bus, self.profile = source, bus, profile
        self.window_s = window_s
        self.enter_score, self.exit_score = enter_score, exit_score
        self.min_active_ms, self.refractory_ms, self.merge_gap_ms = (
            min_active_ms,
            refractory_ms,
            merge_gap_ms,
        )
        self.max_lost_grabs, self.poll_interval_s = max_lost_grabs, poll_interval_s
        self.session_dir = Path(session_dir) if session_dir else None
        self.session_id = session_id or ""
        parameters = (
            {}
            if profile
            else {
                "enter_score": enter_score,
                "exit_score": exit_score,
                "min_active_ms": min_active_ms,
                "refractory_ms": refractory_ms,
                "merge_gap_ms": merge_gap_ms,
            }
        )
        self.pipeline = MeasurementPipeline(profile, retain=False, **parameters)
        self.detector = self.pipeline.motion
        self.signal_window: deque = deque(maxlen=400)
        self.completed_cycles = 0
        self.lost_grabs, self.camera_down = 0, False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_no = 0
        self._t0 = time.monotonic() * 1000
        self._last_image = None
        self._preview_lock = threading.Lock()
        self.recorder: SegmentedRecorder | None = None
        self.recording_error: str | None = None
        self._event_fh = None
        self._measurement_fh = None
        self.event_seq = 0
        self._finished = False
        if self.session_dir:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self._event_fh = (self.session_dir / "live-events.jsonl").open("x")
            self._measurement_fh = (self.session_dir / "measurement-events.jsonl").open("x")
            self.pipeline.event_sink = self._persist_measurement
            fps = int((profile or {}).get("camera", {}).get("fps", 30))
            try:
                self.recorder = SegmentedRecorder(self.session_dir / "video", self.session_id, fps)
            except Exception:
                self._event_fh.close()
                self._measurement_fh.close()
                self.source.close()
                raise

    def _persist_measurement(self, event: dict) -> None:
        self._measurement_fh.write(json.dumps(event, sort_keys=True) + "\n")
        self._measurement_fh.flush()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if self._finished:
            raise RuntimeError("a stopped capture requires a new session")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                self.source.close()
                self._thread.join(timeout=1)
        else:
            self._finish()

    def _emit(self, etype: str, capture_ms: float, **payload) -> None:
        self.event_seq += 1
        event = LiveEvent(
            etype,
            capture_ms,
            0,
            {**payload, "event_seq": self.event_seq, "session_id": self.session_id},
        )
        if self._event_fh is not None:
            record = {"type": etype, **event.payload}
            self._event_fh.write(json.dumps(record, sort_keys=True) + "\n")
            self._event_fh.flush()
        self.bus.publish(event)

    def _process_frame(self, frame: Frame) -> None:
        receipt = frame.receipt_ms if frame.receipt_ms is not None else time.monotonic() * 1000
        if self.recorder and self.recorder.status == "recording":
            try:
                self.recorder.consume(frame)
            except RecordingUnavailable as exc:
                self.recording_error = str(exc)
                self._emit("recording_failed", receipt, reason=str(exc), ms=frame.ms)
        if self.recording_error:
            frame.quality_flags = (*frame.quality_flags, "RECORDING_UNAVAILABLE")
        with self._preview_lock:
            self._last_image = frame.image.copy()
        events = self.pipeline.consume(frame)
        self.completed_cycles = self.pipeline.completed_cycles
        for event in events:
            details = {
                key: value
                for key, value in event.items()
                if key not in ("type", "event_seq", "session_id")
            }
            self._emit(event["type"], receipt, **details)
        if frame.frame_no % 30 == 0:
            result = self.pipeline.result()
            self._emit(
                "heartbeat",
                receipt,
                ms=frame.ms,
                completed_cycles=self.completed_cycles,
                coverage=result["coverage"],
                quality_flags=result["quality_flags"],
                recording_state=self.recorder.status if self.recorder else "disabled",
            )

    def _process_image(self, img, capture_ms: float) -> None:
        """Compatibility seam for deterministic injected observations."""
        self._process_frame(
            Frame(
                self._frame_no,
                capture_ms,
                img,
                self.session_id,
                clock_basis="injected",
                receipt_ms=time.monotonic() * 1000,
            )
        )
        self._frame_no += 1

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            end_ms = self.pipeline.last.ms if self.pipeline.last else None
            if self.pipeline.last and self.pipeline.last.clock_basis != "injected":
                end_ms = max(end_ms, time.monotonic() * 1000 - self._t0)
            if self.pipeline.last is not None:
                for event in self.pipeline.finish(end_ms):
                    details = {
                        key: value
                        for key, value in event.items()
                        if key not in ("type", "event_seq", "session_id")
                    }
                    self._emit(event["type"], time.monotonic() * 1000, **details)
            if self.recorder:
                try:
                    self.recorder.stop(end_ms)
                except Exception as exc:
                    self.recording_error = str(exc)
                    self.recorder.status = "interrupted"
                    self.recorder.error = str(exc)
                    self.recorder._write_manifest()
            if self.session_dir:
                status_path = self.session_dir / "capture-status.json"
                temporary = status_path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(self.snapshot(), indent=2))
                temporary.replace(status_path)
        finally:
            self.source.close()
            if self._event_fh:
                self._event_fh.close()
                self._event_fh = None
            if self._measurement_fh:
                self._measurement_fh.close()
                self._measurement_fh = None

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                ok, image = self.source.read()
                now = time.monotonic() * 1000
                if not ok:
                    self.lost_grabs += 1
                    if not self.camera_down and self.lost_grabs >= self.max_lost_grabs:
                        self.camera_down = True
                        self._emit("camera_lost", now, consecutive_failures=self.lost_grabs)
                    self._stop.wait(min(0.5, self.lost_grabs * self.poll_interval_s))
                    continue
                if self.camera_down:
                    self.camera_down = False
                    self._emit("camera_restored", now)
                self.lost_grabs = 0
                frame = Frame(
                    self._frame_no,
                    now - self._t0,
                    image,
                    self.session_id,
                    clock_basis="synthetic"
                    if getattr(self.source, "synthetic", False)
                    else "host_receipt_monotonic",
                    receipt_ms=now,
                )
                self._frame_no += 1
                self._process_frame(frame)
                self._stop.wait(self.poll_interval_s)
        except Exception as exc:
            self.recording_error = str(exc)
            if self.recorder:
                self.recorder.status = "interrupted"
                self.recorder.error = str(exc)
            self._emit("capture_failed", time.monotonic() * 1000, reason=str(exc))
        finally:
            self._finish()

    def snapshot(self) -> dict:
        result = self.pipeline.result()
        return {
            "session_id": self.session_id,
            "running": bool(self._thread and self._thread.is_alive() and not self._stop.is_set()),
            "completed_cycles": self.completed_cycles,
            "event_seq": self.event_seq,
            "camera_down": self.camera_down,
            "coverage": result["coverage"],
            "quality_flags": result["quality_flags"],
            "recording_error": self.recording_error,
            "recording_state": self.recorder.status if self.recorder else "disabled",
            "recording_manifest": str(self.recorder.manifest_path) if self.recorder else None,
            "negotiated_camera": getattr(self.source, "negotiated", None),
            "synthetic": bool(getattr(self.source, "synthetic", False)),
        }

    def preview_jpeg(self) -> bytes | None:
        import cv2

        with self._preview_lock:
            if self._last_image is None:
                return None
            image = self._last_image.copy()
        ok, encoded = cv2.imencode(".jpg", image)
        return encoded.tobytes() if ok else None


def make_source(
    device_index: int | None = None,
    synthetic: bool = False,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
):
    if synthetic:
        return SyntheticLiveSource([(500, 2500)], fps, width, height)
    if device_index is None:
        raise ValueError("select an explicit camera device or explicit synthetic mode")
    return OpenCvLiveSource(device_index, width, height, fps)
