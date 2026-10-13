#!/usr/bin/env python
"""Run camera-only cycle detection on a Phase 1 session directory (E2).

Usage:
  python tools/run_vision.py datasets/sessions/<sid> [--tolerance-ms 500]

Finds the recorded MP4, replays it on the frame-index clock, segments cycles,
evaluates against hidden ground truth and writes vision_report.json.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.cv.signoff_cv.evaluate import evaluate_session  # noqa: E402
from packages.cv.signoff_cv.motion import MotionDetector  # noqa: E402
from packages.cv.signoff_cv.segmenter import detect_idle, segment_cycles  # noqa: E402
from packages.cv.signoff_cv.source import VideoFileSource  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session_dir", type=Path)
    ap.add_argument("--tolerance-ms", type=float, default=500.0)
    args = ap.parse_args()

    session = args.session_dir
    video_dir = session / "video"
    videos = sorted(video_dir.glob("*.mp4")) if video_dir.exists() else []
    if not videos:
        print(f"error: no .mp4 under {video_dir}", file=sys.stderr)
        return 2

    src = VideoFileSource(videos[0])
    det = MotionDetector()
    signals = []
    for frame in src.frames():
        signals.append(det.process(frame))
    src.close()

    cycles = segment_cycles(signals)
    idles = detect_idle(signals)
    report = evaluate_session(session, [(c.start_ms, c.end_ms) for c in cycles],
                              tolerance_ms=args.tolerance_ms)
    report["idle_episodes"] = len(idles)

    dest = session / "vision_report.json"
    dest.write_text(__import__("json").dumps(report, indent=2))
    print(f"detected {len(cycles)} cycles / {report['ground_truth_cycles']} "
          f"ground truth -> F1={report['f1']}")
    print(f"report: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
