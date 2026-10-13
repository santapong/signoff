"""Durable, bounded one-worker jobs and immutable evidence publication.

Inference receives only observations and a frozen profile. Human reviews are
read separately for outcome metrics. Queued work survives restart; interrupted
work is never presented as complete and a rerun always receives a new ID.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import queue
import subprocess
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from packages.domain.ids import make_id


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: Path) -> tuple[int, int, int, int]:
    item = path.stat()
    return item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns


@lru_cache(maxsize=4096)
def _verified_hash(filename: str, identity: tuple[int, int, int, int]) -> str:
    path = Path(filename)
    value = sha256(path)
    if file_identity(path) != identity:
        raise ValueError("evidence changed while checking integrity")
    return value


def verified_hash(path: Path) -> str:
    """Reuse a verified hash only while inode/size/mtime/ctime are unchanged."""
    return _verified_hash(str(path.resolve()), file_identity(path))


def latest_reviews(session_dir: Path) -> tuple[list[dict], str | None]:
    """Freeze the latest decision per cycle, without modifying detections."""
    rows: dict[int, dict] = {}
    revision = None
    for path in sorted((session_dir / "reviews").glob("*.json")):
        review = json.loads(path.read_text())
        revision = review["revision_id"]
        for row in review["cycles"]:
            rows[row["cycle"]] = row
    return list(rows.values()), revision


def default_analyzer(path: Path, profile: dict | None, clock_path: Path | None) -> dict:
    from packages.cv.signoff_cv.pipeline import analyze_video

    return analyze_video(path, profile=profile, clock_path=clock_path)


def code_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class JobStore:
    def __init__(self, root: Path, analyzer: Callable | None = None):
        self.root = Path(root).resolve()
        self.analyzer = analyzer or default_analyzer
        self.pending: queue.Queue[str | None] = queue.Queue(maxsize=32)
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.worker_lock = None
        self._acquire_worker_lock()
        self.recover()

    def _acquire_worker_lock(self) -> None:
        if self.worker_lock is not None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        handle = (self.root / ".analysis-worker.lock").open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("another application process owns the analysis worker") from exc
        self.worker_lock = handle

    def _release_worker_lock(self) -> None:
        if self.worker_lock is not None:
            fcntl.flock(self.worker_lock, fcntl.LOCK_UN)
            self.worker_lock.close()
            self.worker_lock = None

    def recover(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for path in sorted((self.root / "runs").glob("*/run.json")):
            item = json.loads(path.read_text())
            if item["state"] == "running":
                item.update(
                    state="interrupted",
                    reason="application restarted during analysis",
                    finished_at=utcnow(),
                )
                atomic_json(path, item)
            elif item["state"] == "queued":
                self.pending.put_nowait(item["run_id"])
        for path in (self.root / "sessions").glob("*/session.json"):
            item = json.loads(path.read_text())
            if item.get("recording_state") == "running":
                item.update(
                    recording_state="interrupted",
                    quality_flags=[
                        *item.get("quality_flags", []),
                        "capture_interrupted_on_restart",
                    ],
                )
                atomic_json(path, item)

    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self._acquire_worker_lock()
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, name="signoff-analysis", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        if not self.thread or not self.thread.is_alive():
            self._release_worker_lock()

    def enqueue(self, session: dict, profile: dict | None = None) -> dict:
        with self.lock:
            if self.pending.full():
                raise ValueError("analysis queue is full; try after a job finishes")
            sid = session["session_id"]
            reviews, revision = latest_reviews(self.root / "sessions" / sid)
            run_id = make_id("run")
            item = {
                "schema_version": 2,
                "run_id": run_id,
                "session_id": sid,
                "state": "queued",
                "progress": 0,
                "created_at": utcnow(),
                "session": session,
                "profile": profile,
                "reviewed_cycles": reviews,
                "annotation_revision": revision,
                "status_url": f"/runs/{run_id}",
                "artifacts": {},
            }
            atomic_json(self.root / "runs" / run_id / "run.json", item)
            self.pending.put_nowait(run_id)
            self.start()
            return item

    def get(self, run_id: str) -> dict:
        path = self.root / "runs" / run_id / "run.json"
        if not path.is_file():
            raise FileNotFoundError(run_id)
        return json.loads(path.read_text())

    def _loop(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    run_id = self.pending.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    self.execute(run_id)
                finally:
                    self.pending.task_done()
        finally:
            if self.stop_event.is_set():
                self._release_worker_lock()

    def execute(self, run_id: str) -> None:
        item = self.get(run_id)
        if item["state"] != "queued":
            return
        path = self.root / "runs" / run_id
        item.update(state="running", progress=10, started_at=utcnow())
        atomic_json(path / "run.json", item)
        try:
            from packages.metrics.signoff_metrics import session_metrics
            from packages.reporting.signoff_report import write_evidence_report

            session = item["session"]
            session_dir = self.root / "sessions" / session["session_id"]
            media = session_dir / "video" / session["video_file"]
            if media.resolve().parent != (session_dir / "video").resolve():
                raise ValueError("invalid video path")
            if sha256(media) != session["video_sha256"]:
                raise ValueError("source evidence hash changed")
            source_files = {
                p.relative_to(session_dir).as_posix(): sha256(p)
                for p in sorted((session_dir / "video").rglob("*"))
                if p.is_file()
            }
            if any(p.is_symlink() for p in (session_dir / "video").rglob("*")):
                raise ValueError("source evidence links are unsupported")
            result = self.analyzer(media, item["profile"], None)
            if any(sha256(session_dir / name) != digest for name, digest in source_files.items()):
                raise ValueError("source evidence changed during analysis")
            cycles = result["detected_cycles"]
            cycle_ids = {cycle.get("cycle_id") for cycle in cycles if cycle.get("cycle_id")}
            reviewed = [row for row in item["reviewed_cycles"] if row.get("cycle_id") in cycle_ids]
            metrics = session_metrics(
                [],
                cycles,
                result.get("idle_intervals", []),
                session_duration_ms=result["video_duration_ms"],
                reviewed_cycles=reviewed,
                coverage=result.get("coverage"),
                unknown_intervals=result.get("unknown_intervals"),
            )
            provenance = {
                **result.get("provenance", {}),
                "code_sha": code_sha(),
                "environment": {
                    **result.get("provenance", {}).get("environment", {}),
                    "python": platform.python_version(),
                },
                "source_sha256": session["video_sha256"],
                "source_files": source_files,
                "profile": item["profile"],
                "qualification": "unqualified_software_run",
            }
            analysis = {
                **result,
                "schema_version": 2,
                "session_id": session["session_id"],
                "run_id": run_id,
                "metrics": metrics,
                "provenance": provenance,
            }
            staging = path / "artifacts.pending"
            staging.mkdir()
            add_evidence_clips(staging, media, cycles, run_id)
            atomic_json(staging / "analysis.json", analysis)
            write_evidence_report(
                staging,
                session,
                metrics,
                detected_cycles=cycles,
                provenance=provenance,
                run_id=run_id,
                annotation_revision=item["annotation_revision"],
            )
            manifest = {p.name: sha256(p) for p in sorted(staging.iterdir()) if p.is_file()}
            atomic_json(staging / "manifest.json", {"schema_version": 2, "files": manifest})
            staging.rename(path / "artifacts")
            item.update(
                state="complete",
                progress=100,
                finished_at=utcnow(),
                artifacts={name: f"/runs/{run_id}/artifacts/{name}" for name in manifest},
            )
            atomic_json(path / "run.json", item)
            atomic_json(session_dir / "latest_run.json", {"run_id": run_id})
        except Exception as exc:
            item.update(
                state="failed",
                progress=0,
                finished_at=utcnow(),
                reason=f"{type(exc).__name__}: {exc}",
            )
            atomic_json(path / "run.json", item)

    def artifact(self, run_id: str, name: str) -> Path:
        item = self.get(run_id)
        if item["state"] != "complete":
            raise ValueError("run artifacts are not complete")
        root = self.root / "runs" / run_id / "artifacts"
        manifest = json.loads((root / "manifest.json").read_text())["files"]
        if name not in manifest:
            raise FileNotFoundError(name)
        # Validate every referenced artifact before making a frozen report available.
        for member, expected in manifest.items():
            target = root / member
            if target.is_symlink() or target.resolve().parent != root.resolve():
                raise ValueError("invalid evidence artifact path")
            if not target.is_file() or verified_hash(target) != expected:
                raise ValueError("evidence integrity check failed")
        analysis = json.loads((root / "analysis.json").read_text())
        session_dir = self.root / "sessions" / item["session_id"]
        for member, expected in analysis["provenance"].get("source_files", {}).items():
            target = session_dir / member
            if not target.resolve().is_relative_to(session_dir.resolve()) or target.is_symlink():
                raise ValueError("invalid source evidence path")
            if not target.is_file() or verified_hash(target) != expected:
                raise ValueError("source evidence integrity check failed")
        return root / name


def add_evidence_clips(staging: Path, media: Path, cycles: list[dict], run_id: str) -> None:
    """Derived browser clips; measurement boundaries remain in source-clock ms."""
    if not cycles:
        return
    if media.name.endswith(".manifest.json"):
        segments = json.loads(media.read_text())["segments"]
        sources = [
            {
                "path": media.parent / segment["file"],
                "start_ms": segment["start_ms"],
                "end_ms": segment["end_ms"],
            }
            for segment in segments
        ]
    else:
        sources = [{"path": media, "start_ms": 0, "end_ms": float("inf")}]
    for cycle in cycles:
        start, end = cycle["start_ms"], cycle["end_ms"]
        parts = []
        filename = f"cycle-{int(cycle['cycle']):06d}.mp4"
        destination = staging / filename
        for index, source in enumerate(sources):
            left, right = max(start, source["start_ms"]), min(end, source["end_ms"])
            if right <= left:
                continue
            part = staging / f".cycle-{int(cycle['cycle']):06d}-part-{index:05d}.mp4"
            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-ss",
                str((left - source["start_ms"]) / 1000),
                "-i",
                str(source["path"]),
                "-t",
                str((right - left) / 1000),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "24",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-y",
                str(part),
            ]
            subprocess.run(
                command, check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
            parts.append(part)
        if not parts:
            raise ValueError("cycle does not resolve to a recorded evidence segment")
        if len(parts) == 1:
            parts[0].rename(destination)
        else:
            # Controlled basenames contain no user text or shell interpretation.
            listing = staging / ".clip-parts.txt"
            listing.write_text("".join(f"file '{part.name}'\n" for part in parts))
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-f",
                    "concat",
                    "-safe",
                    "1",
                    "-i",
                    str(listing),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(destination),
                ],
                check=True,
                timeout=120,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            listing.unlink()
            for part in parts:
                part.unlink()
        cycle["evidence"] = {
            "url": f"/runs/{run_id}/artifacts/{filename}",
            "clip_sha256": sha256(destination),
            "derived_viewing_media": True,
            "source_start_ms": start,
            "source_end_ms": end,
            "clip_start_ms": 0,
        }
