"""Single-owner capture → archive + shared measurement → durable live events."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from packages.cv.signoff_cv.pipeline import MeasurementPipeline
from packages.cv.signoff_cv.source import Frame
from services.live_worker.camera import (
    DeadlinePacer,
    MarkerReadiness,
    discover_cameras,
    disk_headroom,
)
from services.live_worker.soak import SoakSamples, canonical_hash
from services.video_worker.recorder import RecordingUnavailable, SegmentedRecorder, sha256_file

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
        self.device_index = None
        self.requested = {"width": width, "height": height, "fps": fps}
        self.negotiated = {"width": width, "height": height, "fps": fps}

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
        self.device_index = device_index
        self.requested = {"width": width, "height": height, "fps": fps}
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
        self.device_metadata = next(
            (item for item in discover_cameras() if item["device_index"] == device_index), {}
        )

    def read(self):
        ok, image = self.cap.read()
        return ok, image if ok else None

    def close(self) -> None:
        self.cap.release()


class LiveEngine:
    """One capture owner with explicit preview and recording modes."""

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
        mode: str = "recording",
        data_dir: Path | None = None,
        source_kind: str = "unknown",
        capture_attested_by: str | None = None,
    ):
        if mode not in {"preview", "recording"}:
            raise ValueError("initial camera mode must be preview or recording")
        if mode == "preview" and (session_dir or session_id):
            raise ValueError("preview cannot create a session or archive")
        self.source, self.bus, self.profile = source, bus, profile
        self.mode = mode
        self.window_s = window_s
        self.enter_score, self.exit_score = enter_score, exit_score
        self.min_active_ms, self.refractory_ms, self.merge_gap_ms = (
            min_active_ms,
            refractory_ms,
            merge_gap_ms,
        )
        self.max_lost_grabs, self.poll_interval_s = max_lost_grabs, poll_interval_s
        self.session_dir = None
        self.data_dir = Path(data_dir or session_dir or ".")
        self.session_id = ""
        self.source_kind = "simulation" if getattr(source, "synthetic", False) else source_kind
        self.capture_attested_by = capture_attested_by
        self._parameters = (
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
        self.pipeline = MeasurementPipeline(profile, retain=False, **self._parameters)
        self.detector = self.pipeline.motion
        self.signal_window: deque = deque(maxlen=400)
        self.completed_cycles = 0
        self.lost_grabs, self.camera_down = 0, False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_no = 0
        self._t0 = time.monotonic() * 1000
        self._opened_at = self._utcnow()
        self._recording_started_at: str | None = None
        self._last_image = None
        self._last_receipt_ms: float | None = None
        self._preview_lock = threading.Lock()
        self._processing_lock = threading.RLock()
        self._observation_times: deque[float] = deque(maxlen=120)
        self._markers = MarkerReadiness()
        self.recorder: SegmentedRecorder | None = None
        self.recording_error: str | None = None
        self._event_fh = None
        self._measurement_fh = None
        self.event_seq = 0
        self._finished = False
        self.runtime_profile: dict | None = None
        self._soak = SoakSamples()
        if session_dir:
            try:
                self._attach_recording(profile, Path(session_dir), session_id or "")
            except Exception:
                self.source.close()
                raise

    @staticmethod
    def _utcnow() -> str:
        return datetime.now(UTC).isoformat()

    def _actual_dimensions(self) -> tuple[int, int] | None:
        if self._last_image is not None:
            height, width = self._last_image.shape[:2]
            return width, height
        negotiated = getattr(self.source, "negotiated", {})
        width, height = negotiated.get("width", 0), negotiated.get("height", 0)
        return (int(width), int(height)) if width and height else None

    def _attach_recording(self, profile: dict | None, session_dir: Path, session_id: str) -> None:
        from packages.domain.provenance import source_code_sha

        # Resolve codec imports before the measurement clock, outside frame processing.
        __import__("av")

        pipeline = MeasurementPipeline(
            profile, retain=False, **({} if profile else self._parameters)
        )
        normalized = pipeline.profile
        actual = self._actual_dimensions()
        if (
            normalized
            and actual
            and actual != (normalized["camera"]["width"], normalized["camera"]["height"])
        ):
            raise ValueError(
                "Profile resolution differs from captured pixels; save actual width and height."
            )
        fps = int((normalized or {}).get("camera", {}).get("fps", 30))
        # Archive preflight precedes new log publication. Failure leaves preview ownership intact.
        archive = SegmentedRecorder(session_dir / "video", session_id, fps)
        event_fh = measurement_fh = None
        try:
            event_fh = (session_dir / "live-events.jsonl").open("x")
            measurement_fh = (session_dir / "measurement-events.jsonl").open("x")
            runtime = {
                "schema_version": 3,
                "session_id": session_id,
                "profile_hash": (normalized or {}).get("profile_hash"),
                "configuration_hash": pipeline.configuration_hash,
                "code_sha": source_code_sha(),
                "source_kind": self.source_kind,
                "capture_attested_by": self.capture_attested_by,
                "source_identity": self.source_identity(),
                "actual_resolution": list(actual) if actual else None,
                "negotiated_camera": getattr(self.source, "negotiated", None),
                "timestamp_basis": "synthetic"
                if getattr(self.source, "synthetic", False)
                else "host_receipt_monotonic",
                "clock_origin": "recording_start",
                "profile": normalized,
            }
            runtime["runtime_profile_hash"] = canonical_hash(runtime)
            self._write_json(session_dir / "capture-profile.json", runtime)
        except Exception:
            if event_fh:
                event_fh.close()
            if measurement_fh:
                measurement_fh.close()
            archive.status = "interrupted"
            archive.stop()
            raise
        self.pipeline, self.profile = pipeline, normalized
        self.detector = pipeline.motion
        self.runtime_profile = runtime
        self.session_dir, self.session_id = session_dir, session_id
        self.recorder = archive
        self._event_fh, self._measurement_fh = event_fh, measurement_fh
        self.pipeline.event_sink = self._persist_measurement
        self._frame_no, self.completed_cycles, self.event_seq = 0, 0, 0
        self._t0 = time.monotonic() * 1000
        self._recording_started_at = self._utcnow()
        self._observation_times.clear()
        self._soak = SoakSamples()
        self.recording_error = None
        self.mode = "recording"
        self.poll_interval_s = 1 / fps

    def begin_recording(
        self,
        profile: dict,
        session_dir: Path,
        session_id: str,
        source_kind: str = "unknown",
        capture_attested_by: str | None = None,
    ) -> None:
        with self._processing_lock:
            if self.mode != "preview" or self._finished:
                raise ValueError("camera must be in active preview before starting a recording")
            if self._last_image is None:
                raise ValueError(
                    "wait for a captured preview frame before saving the profile and recording"
                )
            self.source_kind = (
                "simulation" if getattr(self.source, "synthetic", False) else source_kind
            )
            self.capture_attested_by = capture_attested_by
            self._attach_recording(profile, Path(session_dir), session_id)

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))
        temporary.replace(path)

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
            self._event_fh.write(
                json.dumps({"type": etype, **event.payload}, sort_keys=True) + "\n"
            )
            self._event_fh.flush()
        self.bus.publish(event)
        if etype == "cycle_completed":
            self._soak.event(max(0.0, event.latency_ms))

    def _process_frame(self, frame: Frame) -> None:
        receipt = frame.receipt_ms if frame.receipt_ms is not None else time.monotonic() * 1000
        self._markers.consume(frame.image, receipt)
        with self._preview_lock:
            self._last_image = frame.image.copy()
            self._last_receipt_ms = receipt
        self._observation_times.append(receipt)
        if self.mode == "preview":
            return
        archived = self.recorder is None
        if self.recorder and self.recorder.status == "recording":
            try:
                self.recorder.consume(frame)
                archived = True
            except RecordingUnavailable as exc:
                self.recording_error = str(exc)
                self._emit("recording_failed", receipt, reason=str(exc), ms=frame.ms)
        if self.recording_error:
            frame.quality_flags = (*frame.quality_flags, "RECORDING_UNAVAILABLE")
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
        if archived:
            self._soak.observation(time.monotonic() * 1000 - receipt)

    def _process_image(self, img, capture_ms: float) -> None:
        """Compatibility seam for deterministic injected observations."""
        with self._processing_lock:
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

    def _write_soak(self) -> None:
        if not self.recorder or not self.session_dir:
            return
        from packages.cv.signoff_cv.pipeline import _manifest_sources

        manifest_path = self.recorder.manifest_path
        manifest = json.loads(manifest_path.read_text())
        integrity = False
        try:
            _manifest_sources(manifest_path)
            integrity = manifest.get("status") == "complete" and bool(manifest.get("segments"))
        except (ValueError, OSError, KeyError):
            pass
        result = self.pipeline.result()
        receipt = {
            "schema_version": 3,
            "kind": "live_camera_soak",
            "session_id": self.session_id,
            "source_kind": self.source_kind,
            "capture_attested_by": self.capture_attested_by,
            "real_observations": self._soak.sample_count > 0,
            "started_at": self._recording_started_at,
            "finished_at": self._utcnow(),
            "duration_ms": manifest.get("end_ms") or 0,
            "observation_count": sum(
                item.get("frames", 0) for item in manifest.get("segments", [])
            ),
            "processed_observation_count": result["coverage"]["observation_count"],
            "coverage": result["coverage"],
            "quality_flags": result["quality_flags"],
            "scope": {
                key: (self.runtime_profile or {}).get(key)
                for key in ("profile_hash", "configuration_hash", "code_sha")
            },
            "latency": self._soak.snapshot(),
            "recording": {
                "manifest_sha256": sha256_file(manifest_path),
                "integrity_verified": integrity,
                "status": manifest.get("status"),
            },
            "source_hashes": [
                {key: segment.get(key) for key in ("file", "sha256", "clock_file", "clock_sha256")}
                for segment in manifest.get("segments", [])
            ],
            "runtime_profile_hash": (self.runtime_profile or {}).get("runtime_profile_hash"),
            "clock_basis": result["provenance"]["timestamp_basis"],
            "limitations": [
                "Host receipt timing is not sensor exposure timing.",
                "A short or simulated capture cannot establish the physical two-hour soak.",
            ],
        }
        receipt["receipt_hash"] = canonical_hash(receipt)
        self._write_json(self.session_dir / "live-soak.json", receipt)

    def _finish(self) -> None:
        with self._processing_lock:
            if self._finished:
                return
            self._finished = True
            try:
                end_ms = self.pipeline.last.ms if self.pipeline.last else None
                if self.pipeline.last and self.pipeline.last.clock_basis != "injected":
                    end_ms = max(end_ms, time.monotonic() * 1000 - self._t0)
                if self.mode == "recording" and self.pipeline.last is not None:
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
                        self._write_soak()
                    except Exception as exc:
                        self.recording_error = str(exc)
                        self.recorder.status = "interrupted"
                        self.recorder.error = str(exc)
                        self.recorder._write_manifest()
                self.mode = "closed"
                if self.session_dir:
                    self._write_json(self.session_dir / "capture-status.json", self.snapshot())
            finally:
                self.mode = "closed"
                self.source.close()
                for name in ("_event_fh", "_measurement_fh"):
                    handle = getattr(self, name)
                    if handle:
                        handle.close()
                        setattr(self, name, None)

    def _run(self) -> None:
        pacer = DeadlinePacer(self.poll_interval_s, time.monotonic)
        try:
            while not self._stop.is_set():
                with self._processing_lock:
                    ok, image = self.source.read()
                    now = time.monotonic() * 1000
                    if ok:
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
                    else:
                        self.lost_grabs += 1
                        if not self.camera_down and self.lost_grabs >= self.max_lost_grabs:
                            self.camera_down = True
                            self._emit("camera_lost", now, consecutive_failures=self.lost_grabs)
                if not ok:
                    self._stop.wait(min(0.5, self.lost_grabs * self.poll_interval_s))
                    pacer.reset()
                else:
                    pacer.period_s = self.poll_interval_s
                    self._stop.wait(pacer.remaining())
        except Exception as exc:
            self.recording_error = str(exc)
            if self.recorder:
                self.recorder.status = "interrupted"
                self.recorder.error = str(exc)
            self._emit("capture_failed", time.monotonic() * 1000, reason=str(exc))
        finally:
            self._finish()

    def source_identity(self) -> dict:
        return {
            **getattr(self.source, "device_metadata", {}),
            "source": "synthetic" if getattr(self.source, "synthetic", False) else "usb",
            "device_index": getattr(self.source, "device_index", None),
            "path": f"/dev/video{self.source.device_index}"
            if getattr(self.source, "device_index", None) is not None
            else None,
        }

    def readiness(self) -> dict:
        actual = self._actual_dimensions()
        times = list(self._observation_times)
        observed_fps = (
            (len(times) - 1) * 1000 / (times[-1] - times[0])
            if len(times) >= 2 and times[-1] > times[0]
            else None
        )
        markers = self._markers.snapshot(self.profile)
        free = disk_headroom(self.data_dir)
        opened = self.mode != "closed" and not self._finished
        frame_age = (
            time.monotonic() * 1000 - self._last_receipt_ms if self._last_receipt_ms else None
        )
        connected = (
            opened
            and not self.camera_down
            and frame_age is not None
            and frame_age <= max(500, self.poll_interval_s * 3000)
        )
        profile_valid = (
            None
            if self.profile is None
            else bool(
                actual
                and actual == (self.profile["camera"]["width"], self.profile["camera"]["height"])
                and not getattr(self.pipeline.detector, "invalidated", False)
            )
        )
        rate_ok = observed_fps >= 10 if observed_fps is not None and len(times) >= 10 else None
        disk_start_ok = free >= 5 * (1 << 30) if free is not None else False
        issues = []
        if not connected:
            issues.append(
                {
                    "code": "CAMERA_NOT_READY",
                    "message": "No current camera frame is available.",
                    "action": "Check the selected device; close and reopen preview explicitly.",
                }
            )
        if not markers["reference_visible"] or not markers["tool_visible"]:
            issues.append(
                {
                    "code": "MARKER_NOT_VISIBLE",
                    "message": "Both task markers must remain visible for marker measurement.",
                    "action": "Reposition camera/markers without interfering with the station.",
                }
            )
        if profile_valid is False:
            issues.append(
                {
                    "code": "PROFILE_INVALID",
                    "message": "Saved profile does not match the current camera view.",
                    "action": "Stop and save actual preview dimensions/regions in a new profile.",
                }
            )
        if rate_ok is False:
            issues.append(
                {
                    "code": "RATE_LOW",
                    "message": "Observed frame rate is below 10 observations per second.",
                    "action": "Reduce processing load or choose supported camera settings.",
                }
            )
        if not disk_start_ok:
            issues.append(
                {
                    "code": "DISK_LOW",
                    "message": "A new recording needs 5 GiB free disk.",
                    "action": "Choose storage with enough space; preserve prior evidence.",
                }
            )
        if self.recording_error:
            issues.append(
                {
                    "code": "RECORDING_FAILED",
                    "message": self.recording_error,
                    "action": "Preserve prior evidence and correct the problem before restarting.",
                }
            )
        return {
            "camera_connected": connected,
            "frames_received": bool(times),
            "frame_age_ms": frame_age,
            "markers": markers,
            "profile_valid": profile_valid,
            "rate_ok": rate_ok,
            "disk_free_bytes": free,
            "disk_start_ok": disk_start_ok,
            "can_record": connected and disk_start_ok and profile_valid is not False,
            "issues": issues,
            "physically_qualified": False,
            "actual_camera": {
                "device_index": getattr(self.source, "device_index", None),
                "width": actual[0] if actual else None,
                "height": actual[1] if actual else None,
                "requested_fps": getattr(self.source, "requested", {}).get("fps"),
                "negotiated_fps": getattr(self.source, "negotiated", {}).get("fps"),
                "observed_fps": observed_fps,
            },
        }

    def snapshot(self) -> dict:
        result = self.pipeline.result()
        readiness = self.readiness()
        return {
            "mode": self.mode,
            "camera_open": self.mode != "closed" and not self._finished,
            "session_id": self.session_id or None,
            "running": bool(
                self._thread
                and self._thread.is_alive()
                and not self._stop.is_set()
                and self.mode != "closed"
            ),
            "completed_cycles": self.completed_cycles,
            "event_seq": self.event_seq,
            "camera_down": self.camera_down,
            "coverage": result["coverage"],
            "duration_ms": result["video_duration_ms"] if self.mode != "preview" else 0,
            "quality_flags": result["quality_flags"] if self.mode != "preview" else [],
            "recording_error": self.recording_error,
            "recording_state": self.recorder.status if self.recorder else "disabled",
            "recording_manifest": str(self.recorder.manifest_path) if self.recorder else None,
            "negotiated_camera": getattr(self.source, "negotiated", None),
            "actual_camera": readiness["actual_camera"],
            "readiness": readiness,
            "source_identity": self.source_identity(),
            "source_kind": self.source_kind,
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
