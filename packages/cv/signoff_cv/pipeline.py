"""Shared recorded/live measurement path. Reference labels are never inputs."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import subprocess
from collections import deque
from collections.abc import Callable, Iterable
from importlib.metadata import version
from pathlib import Path

from packages.cv.signoff_cv.motion import MotionDetector
from packages.cv.signoff_cv.source import Frame, VideoFileSource
from packages.cv.signoff_cv.task import MarkerTaskDetector, MotionCycleDetector


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _profile_hash(profile: dict | None) -> str:
    if profile and profile.get("profile_hash"):
        return profile["profile_hash"]
    return hashlib.sha256(
        json.dumps(profile or {"method": "motion"}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class MeasurementPipeline:
    """Streaming detector with bounded state; optional retained offline evidence."""

    def __init__(
        self,
        profile: dict | None = None,
        retain: bool = True,
        event_sink: Callable[[dict], None] | None = None,
        **motion_parameters,
    ):
        if profile is not None:
            from packages.domain.profiles import validate_profile

            profile = validate_profile(profile)
        self.profile = profile
        self.retain = retain
        self.event_sink = event_sink
        self.motion = MotionDetector()
        self.method = (profile or {}).get("detector", {}).get("method", "motion")
        parameters = {
            name: value
            for name, value in (profile or {}).get("detector", {}).items()
            if name
            in ("enter_score", "exit_score", "min_active_ms", "refractory_ms", "merge_gap_ms")
        }
        parameters.update(motion_parameters)
        self.detector = (
            MarkerTaskDetector(profile)
            if self.method == "aruco"
            else MotionCycleDetector(_profile_hash(profile), **parameters)
        )
        configuration = {
            "profile_hash": _profile_hash(profile),
            "method": self.method,
            "motion": {
                name: getattr(self.detector, name)
                for name in (
                    "enter_score",
                    "exit_score",
                    "min_active_ms",
                    "refractory_ms",
                    "merge_gap_ms",
                )
            }
            if self.method == "motion"
            else {},
        }
        self.configuration_hash = hashlib.sha256(
            json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.detector.profile_hash = self.configuration_hash
        self.last: Frame | None = None
        self.last_state = "UNKNOWN"
        self.intervals: dict[str, list[dict] | deque] = {
            name: [] if retain else deque(maxlen=128) for name in ("ACTIVE", "IDLE", "UNKNOWN")
        }
        self.durations = dict.fromkeys(self.intervals, 0.0)
        self.cycles: list[dict] | deque = [] if retain else deque(maxlen=128)
        self.partials: list[dict] | deque = [] if retain else deque(maxlen=128)
        self.events: list[dict] | deque = [] if retain else deque(maxlen=128)
        self.quality_flags: set[str] = set()
        self.deltas: deque[float] = deque(maxlen=128)
        self.clock_bases: set[str] = set()
        self.observation_count = 0
        self.usable_count = 0
        self.event_seq = 0
        self.finished = False
        self.video_duration_ms = 0.0

    @property
    def completed_cycles(self) -> int:
        return self.detector.completed

    def _interval(self, state: str, start: float, end: float, reason: str = "") -> None:
        if end <= start:
            return
        self.durations[state] += end - start
        intervals = self.intervals[state]
        if (
            intervals
            and intervals[-1]["end_ms"] == start
            and intervals[-1].get("reason", "") == reason
        ):
            intervals[-1]["end_ms"] = end
        else:
            intervals.append(
                {"start_ms": start, "end_ms": end, **({"reason": reason} if reason else {})}
            )

    def _publish(self, events: list[dict]) -> list[dict]:
        for event in events:
            self.event_seq += 1
            event["event_seq"] = self.event_seq
            self.events.append(event)
            if event["type"] == "cycle_completed":
                self.cycles.append(event)
            elif event["type"] == "cycle_partial":
                self.partials.append(event)
            if self.event_sink:
                self.event_sink(event)
        return events

    def consume(self, frame: Frame) -> list[dict]:
        if self.finished:
            raise RuntimeError("pipeline has already finished")
        if not math.isfinite(frame.ms) or frame.ms < 0:
            raise ValueError("observation timestamp must be finite and nonnegative")
        events = []
        if self.last is not None:
            if frame.ms <= self.last.ms or frame.frame_no <= self.last.frame_no:
                raise ValueError("observations must have increasing timestamps and frame numbers")
            delta = frame.ms - self.last.ms
            self.deltas.append(delta)
            gap_ms = (self.profile or {}).get("detector", {}).get("visibility_gap_ms", 250)
            if delta > gap_ms or frame.frame_no != self.last.frame_no + 1:
                reason = "OBSERVATION_GAP"
                self.quality_flags.add(reason)
                self._interval("UNKNOWN", self.last.ms, frame.ms, reason)
                events += self.detector.gap(frame, reason)
                self.motion.reset()
            else:
                self._interval(self.last_state, self.last.ms, frame.ms)
        elif frame.ms > 0:
            self._interval("UNKNOWN", 0, frame.ms, "INITIAL_OBSERVATION_GAP")
            self.quality_flags.add("INITIAL_OBSERVATION_GAP")
        self.clock_bases.add(frame.clock_basis)
        self.observation_count += 1
        if frame.quality_flags:
            self.quality_flags.update(frame.quality_flags)
            events += self.detector.gap(frame, ",".join(frame.quality_flags))
            state, flags = "UNKNOWN", []
        elif self.method == "aruco":
            new, state, flags = self.detector.process(frame)
            events += new
        else:
            if self.last is not None and self.last.image.shape != frame.image.shape:
                self.motion.reset()
                events += self.detector.gap(frame, "RESOLUTION_CHANGED")
                state, flags = "UNKNOWN", ["RESOLUTION_CHANGED"]
            else:
                score = self.motion.process(frame).score
                new, state, flags = self.detector.process(frame, score)
                events += new
        self.quality_flags.update(flags)
        self.usable_count += state != "UNKNOWN"
        self.last, self.last_state = frame, state
        self.video_duration_ms = frame.ms
        return self._publish(events)

    def finish(self, duration_ms: float | None = None) -> list[dict]:
        if self.finished:
            return []
        self.finished = True
        if self.last is None:
            raise ValueError("cannot analyze an empty observation sequence")
        # Final frame occupies an estimated sample interval unless media duration is known.
        ordered = sorted(self.deltas)
        tail = ordered[len(ordered) // 2] if ordered else 0.0
        end = max(self.last.ms, duration_ms if duration_ms is not None else self.last.ms + tail)
        gap_ms = (self.profile or {}).get("detector", {}).get("visibility_gap_ms", 250)
        if end - self.last.ms > gap_ms:
            self._interval("UNKNOWN", self.last.ms, end, "TRAILING_OBSERVATION_GAP")
            self.quality_flags.add("TRAILING_OBSERVATION_GAP")
        else:
            self._interval(self.last_state, self.last.ms, end)
        self.video_duration_ms = end
        return self._publish(self.detector.finish(self.last))

    def result(self) -> dict:
        duration = self.video_duration_ms
        unknown = self.durations["UNKNOWN"]
        observed = self.durations["ACTIVE"] + self.durations["IDLE"]
        flags = set(self.quality_flags)
        usable_fps = self.usable_count / (duration / 1000) if duration else 0.0
        if usable_fps < 10:
            flags.add("OBSERVATION_RATE_BELOW_10_FPS")
        return {
            "schema_version": 2,
            "detector_method": self.method,
            "detected_cycles": list(self.cycles),
            "partial_cycles": list(self.partials),
            "idle_intervals": list(self.intervals["IDLE"]),
            "visual_activity_intervals": list(self.intervals["ACTIVE"]),
            "unknown_intervals": list(self.intervals["UNKNOWN"]),
            "video_duration_ms": duration,
            "completed_cycles": self.completed_cycles,
            "coverage": {
                "observed_ms": observed,
                "unknown_ms": unknown,
                "usable_ratio": observed / duration if duration else 0.0,
                "usable_fps": usable_fps,
                "observation_count": self.observation_count,
                "clock_basis": sorted(self.clock_bases),
            },
            "quality_flags": sorted(flags),
            "events": list(self.events),
            "provenance": {
                "profile_hash": _profile_hash(self.profile),
                "configuration_hash": self.configuration_hash,
                "observed_fps": self.observation_count / (duration / 1000) if duration else 0.0,
                "timestamp_basis": sorted(self.clock_bases),
                "measurement_basis": "VISIBLE_SEQUENCE"
                if self.method == "aruco"
                else "MOTION_BASELINE",
                "physically_qualified": False,
            },
        }


def analyze_frames(
    frames: Iterable[Frame],
    profile: dict | None = None,
    duration_ms: float | None = None,
    **motion_parameters,
) -> dict:
    pipeline = MeasurementPipeline(profile, **motion_parameters)
    for frame in frames:
        pipeline.consume(frame)
    pipeline.finish(duration_ms)
    return pipeline.result()


def _manifest_sources(path: Path) -> tuple[list[tuple[Path, Path]], dict]:
    manifest = json.loads(path.read_text())
    if manifest.get("status") == "recording":
        raise ValueError("recording must be stopped before replay")
    sources = []
    for segment in manifest.get("segments", []):
        names = (segment["file"], segment["clock_file"])
        for key, name in zip(("sha256", "clock_sha256"), names):
            candidate = path.parent / name
            if Path(name).name != name or candidate.is_symlink() or not candidate.is_file():
                raise ValueError("invalid recording manifest path")
            if _hash(candidate) != segment[key]:
                raise ValueError("recording segment integrity check failed")
        sources.append((path.parent / names[0], path.parent / names[1]))
    if not sources:
        raise ValueError("recording contains no complete segments")
    return sources, manifest


def analyze_video(
    video_path: Path, profile: dict | None = None, clock_path: Path | None = None
) -> dict:
    """Analyze a video or finalized multi-segment recording manifest."""
    video_path = Path(video_path)
    pipeline = MeasurementPipeline(profile)
    hashes = []
    manifest = None
    if video_path.name.endswith(".manifest.json"):
        paths, manifest = _manifest_sources(video_path)
    else:
        paths = [(video_path, clock_path)]
    duration = None
    for path, clock in paths:
        source = VideoFileSource(path, clock)
        try:
            for frame in source.frames():
                pipeline.consume(frame)
            if len(paths) == 1:
                duration = source.duration_ms
            hashes.append(
                {
                    "file": path.name,
                    "sha256": _hash(path),
                    "clock_file": source.clock_path.name if source.clock_path else None,
                    "clock_sha256": _hash(source.clock_path) if source.clock_path else None,
                }
            )
        finally:
            source.close()
    if manifest is not None:
        duration = manifest.get("end_ms")
        if manifest.get("status") != "complete":
            pipeline.quality_flags.add("RECORDING_INTERRUPTED")
        if manifest.get("unavailable_since_ms") is not None:
            pipeline.last_state = "UNKNOWN"
            pipeline.quality_flags.add("RECORDING_UNAVAILABLE")

    pipeline.finish(duration)
    result = pipeline.result()
    result["provenance"]["sources"] = hashes
    result["provenance"]["video_sha256"] = _hash(video_path)
    result["provenance"]["environment"] = {
        "python": platform.python_version(),
        "av": version("av"),
        "numpy": version("numpy"),
        "opencv": __import__("cv2").__version__,
    }
    try:
        result["provenance"]["code_sha"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        result["provenance"]["code_sha"] = None
    if manifest is not None:
        result["provenance"]["manifest_sha256"] = _hash(video_path)
    result["analysis_hash"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return result
