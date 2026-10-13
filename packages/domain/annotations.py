"""Annotation schema and validation (SDD §44).

One JSONL file per (session, annotator). Manual annotations are stored
separately from model predictions and never merged in place (SDD §26).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

Outcome = Literal["OK", "FAILED", "UNKNOWN"]
FailureType = Literal[
    "NONE",
    "GRASP_MISS",
    "OBJECT_SLIP",
    "OBJECT_DROP",
    "DESTINATION_OCCUPIED",
    "INTERRUPTED",
    "OTHER",
]


def make_annotation(
    session_id: str,
    cycle: int,
    cycle_start_ms: float,
    cycle_end_ms: float,
    video_duration_ms: float,
    outcome: Outcome = "UNKNOWN",
    failure_type: FailureType = "NONE",
    annotator: str = "unknown",
    *,
    cycle_id: str | None = None,
    run_id: str | None = None,
    revision_id: str | None = None,
    supersedes: str | None = None,
) -> dict:
    ann = {
        "schema_version": 2,
        "revision_id": revision_id or f"ann_{uuid4().hex}",
        "supersedes": supersedes,
        "run_id": run_id,
        "session": session_id,
        "cycle": cycle,
        "cycle_start_ms": round(cycle_start_ms, 1),
        "cycle_end_ms": round(cycle_end_ms, 1),
        "outcome": outcome,
        "failure_type": failure_type,
        "annotator": annotator,
    }
    if cycle_id is not None:
        ann["cycle_id"] = cycle_id
    validate_annotation(ann, video_duration_ms)
    return ann


def validate_annotation(ann: dict, video_duration_ms: float) -> None:
    if ann["outcome"] not in ("OK", "FAILED", "UNKNOWN"):
        raise ValueError(f"bad outcome {ann['outcome']!r}")
    if ann["failure_type"] not in (
        "NONE",
        "GRASP_MISS",
        "OBJECT_SLIP",
        "OBJECT_DROP",
        "DESTINATION_OCCUPIED",
        "INTERRUPTED",
        "OTHER",
    ):
        raise ValueError(f"bad failure_type {ann['failure_type']!r}")
    if ann["outcome"] == "FAILED" and ann["failure_type"] == "NONE":
        raise ValueError("FAILED outcome requires a failure_type")
    if ann["outcome"] != "FAILED" and ann["failure_type"] != "NONE":
        raise ValueError("only FAILED outcomes may have a failure_type")
    if isinstance(ann["cycle"], bool) or not isinstance(ann["cycle"], int) or ann["cycle"] < 1:
        raise ValueError("cycle must be a positive integer")
    if not isinstance(ann.get("annotator"), str) or not ann["annotator"].strip():
        raise ValueError("annotator must be a nonempty name")
    boundaries = (ann["cycle_start_ms"], ann["cycle_end_ms"], video_duration_ms)
    if not all(math.isfinite(float(value)) for value in boundaries):
        raise ValueError("annotation boundaries and video duration must be finite")
    if ann["cycle_start_ms"] < 0 or video_duration_ms < 0:
        raise ValueError("annotation times must be nonnegative")
    if ann["cycle_start_ms"] >= ann["cycle_end_ms"]:
        raise ValueError("cycle_start_ms must be < cycle_end_ms")
    if ann["cycle_end_ms"] > video_duration_ms + 1.0:
        raise ValueError(
            f"cycle end {ann['cycle_end_ms']} beyond video duration "
            f"{video_duration_ms}ms"
        )


_WRITE_LOCK = Lock()


def write_annotations(path: Path, annotations: list[dict]) -> None:
    """Append revisions without changing earlier predictions or review rows."""
    path = Path(path)
    with _WRITE_LOCK:
        existing = read_annotations(path)
        revisions = {row["revision_id"]: row for row in existing if row.get("revision_id")}
        for ann in annotations:
            identity = ann.get("revision_id")
            if identity and identity in revisions:
                raise ValueError("annotation revision identity already exists")
            prior = ann.get("supersedes")
            if prior:
                if prior not in revisions:
                    raise ValueError("superseded annotation revision does not exist")
                old = revisions[prior]
                keys = ("session", "run_id", "cycle", "cycle_id", "annotator")
                if any(old.get(key) != ann.get(key) for key in keys):
                    raise ValueError("revision must refer to the same cycle, run and annotator")
            if identity:
                revisions[identity] = ann
        payload = "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
                          for row in annotations)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(payload)
            fh.flush()


def read_annotations(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def latest_annotations(annotations: list[dict], *, run_id: str | None = None) -> list[dict]:
    """Select last appended review per annotator; never silently adjudicate people."""
    latest = {}
    for row in annotations:
        if run_id is not None and row.get("run_id") != run_id:
            continue
        key = (row.get("session"), row.get("run_id"), row.get("cycle_id", row.get("cycle")),
               row.get("annotator"))
        latest[key] = row
    return sorted(latest.values(), key=lambda row: (
        str(row.get("session")), str(row.get("cycle_id", row.get("cycle"))),
        str(row.get("annotator"))))
