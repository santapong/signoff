#!/usr/bin/env python
"""Generate a scripted ArUco SIMULATION for upload/review rehearsal.

No camera or robot is accessed. Reference boundaries come from the generation
schedule, independently of Signoff inference. This cannot qualify a real station.

Usage: python tools/generate_demo.py --out data/demo-camera-v2 --lossless
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from packages.cv.signoff_cv.source import Frame  # noqa: E402
from packages.domain.profiles import validate_profile  # noqa: E402
from services.video_worker.recorder import SegmentedRecorder, sha256_file  # noqa: E402


def _image(region: str, markers: dict[int, np.ndarray]) -> np.ndarray:
    image = np.full((240, 320, 3), 255, np.uint8)
    positions = {"HOME": (35, 30), "PICK": (35, 165), "PLACE": (230, 165), "OUTSIDE": (140, 95)}
    x, y = positions[region]
    image[20:60, 220:260] = markers[0][:, :, None]
    image[y : y + 40, x : x + 40] = markers[1][:, :, None]
    cv2.putText(
        image,
        "SIMULATION - NO ROBOT",
        (5, 14),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.36,
        (20, 20, 20),
        1,
        cv2.LINE_8,
    )
    for label, point in (("HOME", (35, 83)), ("PICK", (35, 225)), ("PLACE", (230, 225))):
        cv2.putText(image, label, point, cv2.FONT_HERSHEY_SIMPLEX, 0.4, (60, 60, 60), 1, cv2.LINE_8)
    return image


def generate_demo(output_dir: Path, cycles: int = 4, lossless: bool = False) -> dict:
    if isinstance(cycles, bool) or not isinstance(cycles, int) or not 1 <= cycles <= 5:
        raise ValueError("demo cycles must be an integer from 1 to 5")
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("demo destination must be empty; existing evidence is preserved")
    output_dir.mkdir(parents=True, exist_ok=True)
    fps = 15
    profile = validate_profile(
        {
            "profile_id": "simulation-pick-place",
            "name": "SIMULATION: scripted marker task",
            "camera": {"width": 320, "height": 240, "fps": fps},
            "detector": {
                "method": "aruco",
                "home": [0.05, 0.05, 0.25, 0.3],
                "pick": [0.05, 0.6, 0.25, 0.3],
                "place": [0.65, 0.6, 0.25, 0.3],
            },
        }
    )
    # The schedule owns the reference clock. Never query the detector to label it.
    schedule = ["HOME"] * fps
    reference = []
    for cycle in range(1, cycles + 1):
        start_frame = len(schedule)
        for region, frame_count in (
            ("OUTSIDE", 3),
            ("PICK", 12),
            ("OUTSIDE", 6),
            ("PLACE", 12),
            ("OUTSIDE", 3),
        ):
            schedule.extend([region] * frame_count)
        end_frame = len(schedule)
        schedule.extend(["HOME"] * fps)
        reference.append(
            {
                "schema_version": 2,
                "session": "scripted-demo",
                "cycle": cycle,
                "cycle_start_ms": start_frame * 1000 / fps,
                "cycle_end_ms": end_frame * 1000 / fps,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "outcome": "UNKNOWN",
                "failure_type": "NONE",
                "annotator": "scripted_sequence",
                "annotation_revision": "scripted-v1",
                "source_kind": "simulation",
                "basis": "GENERATION_SCHEDULE",
            }
        )
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    markers = {i: cv2.aruco.generateImageMarker(dictionary, i, 40) for i in (0, 1)}
    video_path = output_dir / "demo.mp4"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 240))
    if not writer.isOpened():
        raise RuntimeError("demo MP4 writer could not open")
    archive = (
        SegmentedRecorder(output_dir / "lossless", "scripted-demo", fps, min_start_bytes=0)
        if lossless
        else None
    )
    try:
        for frame_number, region in enumerate(schedule):
            image = _image(region, markers)
            writer.write(image)
            if archive:
                archive.consume(
                    Frame(
                        frame_number,
                        frame_number * 1000 / fps,
                        image,
                        "scripted-demo",
                        clock_basis="synthetic",
                    )
                )
    finally:
        writer.release()
        if archive:
            archive.stop(len(schedule) * 1000 / fps)
    profile_path = output_dir / "profile.json"
    reference_path = output_dir / "reference.jsonl"
    profile_path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n")
    reference_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in reference))
    manifest = {
        "schema_version": 2,
        "dataset_id": "scripted-camera-demo",
        "source_kind": "simulation",
        "physically_qualified": False,
        "purpose": "software/browser rehearsal only; no physical robot evidence",
        "frozen_at": None,
        "capture_attested_by": None,
        "annotators": ["scripted_sequence"],
        "annotator_kinds": {"scripted_sequence": "script"},
        "generator": {
            "version": 1,
            "cycles": cycles,
            "frames": len(schedule),
            "fps": fps,
            "duration_ms": len(schedule) * 1000 / fps,
        },
        "sessions": [
            {
                "session_id": "scripted-demo",
                "split": "test",
                "video": video_path.name,
                "video_sha256": sha256_file(video_path),
                "profile": profile_path.name,
                "profile_sha256": sha256_file(profile_path),
                "profile_hash": profile["profile_hash"],
                "reference": reference_path.name,
                "reference_sha256": sha256_file(reference_path),
            }
        ],
    }
    (output_dir / "dataset.manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return {
        "source_kind": "simulation",
        "physically_qualified": False,
        "video": str(video_path.resolve()),
        "profile": str(profile_path.resolve()),
        "reference": str(reference_path.resolve()),
        "cycles": cycles,
        "duration_ms": manifest["generator"]["duration_ms"],
        "lossless_manifest": str(archive.manifest_path.resolve()) if archive else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/demo-camera-v2"))
    parser.add_argument("--cycles", type=int, default=4, choices=range(1, 6))
    parser.add_argument("--lossless", action="store_true")
    args = parser.parse_args()
    try:
        result = generate_demo(args.out, args.cycles, args.lossless)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"demo generation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
