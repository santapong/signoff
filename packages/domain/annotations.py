"""Annotation schema and validation (SDD §44).

One JSONL file per (session, annotator). Manual annotations are stored
separately from model predictions and never merged in place (SDD §26).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

Outcome = Literal["OK", "FAILED"]
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
    outcome: Outcome = "OK",
    failure_type: FailureType = "NONE",
    annotator: str = "unknown",
) -> dict:
    ann = {
        "session": session_id,
        "cycle": cycle,
        "cycle_start_ms": round(cycle_start_ms, 1),
        "cycle_end_ms": round(cycle_end_ms, 1),
        "outcome": outcome,
        "failure_type": failure_type,
        "annotator": annotator,
    }
    validate_annotation(ann, video_duration_ms)
    return ann


def validate_annotation(ann: dict, video_duration_ms: float) -> None:
    if ann["outcome"] not in ("OK", "FAILED"):
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
    if ann["cycle_start_ms"] >= ann["cycle_end_ms"]:
        raise ValueError("cycle_start_ms must be < cycle_end_ms")
    if ann["cycle_end_ms"] > video_duration_ms + 1.0:
        raise ValueError(
            f"cycle end {ann['cycle_end_ms']} beyond video duration "
            f"{video_duration_ms}ms"
        )


def write_annotations(path: Path, annotations: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for ann in annotations:
            fh.write(json.dumps(ann, sort_keys=True) + "\n")


def read_annotations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]
