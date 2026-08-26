"""Live monitoring engine: frame source -> motion -> cycle events -> bus.

Runs in a background thread; publishes typed events with latency
instrumentation (capture_ms vs emit_ms, monotonic clock).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from packages.cv.signoff_cv.motion import MotionDetector, MotionSignal
from packages.cv.signoff_cv.segmenter import segment_cycles


LATENCY_BUDGET_MS = 5000.0


@dataclass
class LiveEvent:
    type: str                 # cycle_started | cycle_completed | camera_lost |
                              # camera_restored | heartbeat
    capture_ms: float         # monotonic ts of the triggering frame grab
    emit_ms: float            # monotonic ts of publication
    payload: dict = field(default_factory=dict)

    @property
    def latency_ms(self) -> float:
        return self.emit_ms - self.capture_ms

    def to_dict(self) -> dict:
        return {"type": self.type, "capture_ms": self.capture_ms,
                "emit_ms": self.emit_ms,
                "latency_ms": round(self.latency_ms, 1),
                "within_budget": self.latency_ms < LATENCY_BUDGET_MS,
                **self.payload}


class EventBus:
    """Thread-safe pub/sub over a bounded deque of recent events."""

    def __init__(self, maxlen: int = 500):
        self._events: deque[LiveEvent] = deque(maxlen=maxlen)
        self._subscribers: list[Callable[[LiveEvent], None]] = []
        self._lock = threading.Lock()

    def publish(self, event: LiveEvent) -> None:
        event.emit_ms = time.monotonic() * 1000.0
        with self._lock:
            self._events.append(event)
        for sub in list(self._subscribers):
            try:
                sub(event)
            except Exception:  # subscriber errors must not kill the engine
                pass

    def subscribe(self, fn: Callable[[LiveEvent], None]) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def recent(self, n: int = 50) -> list[LiveEvent]:
        with self._lock:
            return list(self._events)[-n:]


class FrameSourceProtocol:
    """Minimal contract the engine needs from any frame source."""

    def read(self):  # -> (ok, image_or_None)
        raise NotImplementedError

    def close(self) -> None:
        ...


class SyntheticLiveSource:
    """Deterministic synthetic source for tests and smoke runs (no camera)."""

    def __init__(self, activity_windows_ms: list[tuple[float, float]],
                 fps: float = 30.0, width: int = 160, height: int = 120):
        self.windows = activity_windows_ms
        self.fps = fps
        self.t0 = time.monotonic() * 1000.0
        self.width, self.height = width, height
        self.fail_until: float | None = None  # watchdog test hook
        import numpy as np
        self.np = np

    def _active_now(self, ms: float) -> bool:
        rel = (ms - self.t0)
        # loop every 8s so live sessions keep producing cycles
        return any(s <= (rel % 8000.0) <= e for s, e in self.windows)

    def read(self):
        now = time.monotonic() * 1000.0
        if self.fail_until is not None and now < self.fail_until:
            return False, None
        img = self.np.zeros((self.height, self.width, 3), dtype=self.np.uint8)
        if self._active_now(now):
            jx = int((now / 30)) % 15
            jy = int((now / 30)) % 20
            img[40 + jy:80 + jy, 60 - jx:100 - jx] = (255, 255, 255)
        return True, img

    def close(self) -> None:
        pass


class OpenCvLiveSource:
    def __init__(self, device_index: int = 0):
        import cv2
        self.cap = cv2.VideoCapture(device_index)

    def read(self):
        ok, img = self.cap.read()
        return ok, (img if ok else None)

    def close(self) -> None:
        self.cap.release()


class LiveEngine:
    """Consume frames, keep a sliding signal window, emit cycle events."""

    def __init__(self, source, bus: EventBus,
                 window_s: float = 12.0, enter_score: float = 4.0,
                 exit_score: float = 2.0, min_active_ms: float = 400.0,
                 refractory_ms: float = 500.0, merge_gap_ms: float = 300.0,
                 max_lost_grabs: int = 10, poll_interval_s: float = 1/30):
        self.source = source
        self.bus = bus
        self.window_s = window_s
        self.enter_score = enter_score
        self.exit_score = exit_score
        self.min_active_ms = min_active_ms
        self.refractory_ms = refractory_ms
        self.merge_gap_ms = merge_gap_ms
        self.max_lost_grabs = max_lost_grabs
        self.poll_interval_s = poll_interval_s

        self.detector = MotionDetector()
        self.signal_window: deque[MotionSignal] = deque()
        self.completed_cycles = 0
        self.lost_grabs = 0
        self.camera_down = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def _emit(self, etype: str, capture_ms: float, **payload) -> None:
        self.bus.publish(LiveEvent(etype, capture_ms=capture_ms,
                                   emit_ms=0.0, payload=payload))

    def _process_image(self, img, capture_ms: float) -> None:
        from packages.cv.signoff_cv.source import Frame
        frame = Frame(frame_no=int(capture_ms), ms=capture_ms, image=img)
        sig = self.detector.process(frame)
        self.signal_window.append(sig)
        cutoff = capture_ms - self.window_s * 1000.0
        while self.signal_window and self.signal_window[0].ms < cutoff:
            self.signal_window.popleft()

        before = self.completed_cycles
        cycles = segment_cycles(
            list(self.signal_window), enter_score=self.enter_score,
            exit_score=self.exit_score, min_active_ms=self.min_active_ms,
            refractory_ms=self.refractory_ms, merge_gap_ms=self.merge_gap_ms)
        self.completed_cycles = len(cycles)
        if self.completed_cycles > before:
            last = cycles[-1]
            self._emit("cycle_completed", capture_ms,
                       cycle=self.completed_cycles,
                       start_ms=last.start_ms, end_ms=last.end_ms)

    def _run(self) -> None:
        while not self._stop.is_set():
            ok, img = self.source.read()
            now = time.monotonic() * 1000.0
            if not ok:
                self.lost_grabs += 1
                if not self.camera_down and self.lost_grabs >= self.max_lost_grabs:
                    self.camera_down = True
                    self._emit("camera_lost", now,
                               consecutive_failures=self.lost_grabs)
                time.sleep(min(0.5, self.lost_grabs * self.poll_interval_s))
                continue

            if self.camera_down:
                self.camera_down = False
                self.detector.reset()   # scene may have changed while dark
                self._emit("camera_restored", now)
            self.lost_grabs = 0
            self._process_image(img, now)
            time.sleep(self.poll_interval_s)


def make_source(device_index: int | None = None,
                synthetic: bool = True):
    if synthetic or device_index is None:
        return SyntheticLiveSource(activity_windows_ms=[(500, 2500)])
    return OpenCvLiveSource(device_index)
