"""Clock sync check: frame-index clock vs ground-truth clock drift (SDD §19/§40).

Both clocks are monotonic ms from their own epoch. This tool reports the offset
between SESSION_START in the ground truth and the first recorded frame so
annotations can be corrected for recorder start-up latency.

Usage:
  python tools/clock_sync_check.py datasets/sessions/<sid>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    sdir = Path(sys.argv[1])
    gt_path = sdir / ".ground_truth" / "events.jsonl"
    idx_path = sdir / "video" / "frame_index.jsonl"
    if not gt_path.exists() or not idx_path.exists():
        print("missing ground truth or frame index", file=sys.stderr)
        return 1

    events = [json.loads(x) for x in gt_path.read_text().splitlines() if x]
    frames = [json.loads(x) for x in idx_path.read_text().splitlines() if x]
    session_start = next((e["timestamp_ms"] for e in events
                          if e["event"] == "SESSION_START"), None)
    first_frame_ms = frames[0]["ms"] if frames else None

    report = {
        "session_dir": str(sdir),
        "gt_session_start_ms": session_start,
        "first_frame_ms": first_frame_ms,
        "frames": len(frames),
        "last_frame_ms": frames[-1]["ms"] if frames else None,
        "note": "offset = first_frame_ms; add to annotation times to align "
                "with ground-truth epoch",
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
