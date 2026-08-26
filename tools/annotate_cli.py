#!/usr/bin/env python3
"""Keyboard-driven cycle annotation over a recorded video (SDD §44, §26).

Keys:
  SPACE   mark cycle start (green) / end (writes annotation, yellow)
  f       mark current cycle as FAILED (then choose failure type via number key)
  1-6     failure type when flagging a failure
  ←/→     scrub 1 s (shift: 10 s)
  u       undo last annotation
  w       write JSONL and exit
  q/ESC   exit without writing

Usage:
  python tools/annotate_cli.py datasets/sessions/<sid>/video/<vid>.mp4 \
      --session <session_id> --annotator santapong
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cv2  # noqa: E402

from packages.domain.annotations import validate_annotation  # noqa: E402

FAILURE_TYPES = {
    "1": "GRASP_MISS", "2": "OBJECT_SLIP", "3": "OBJECT_DROP",
    "4": "DESTINATION_OCCUPIED", "5": "INTERRUPTED", "6": "OTHER",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("--session", required=True)
    ap.add_argument("--annotator", default="unknown")
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(f"cannot open {args.video}", file=sys.stderr)
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_ms = total_frames / fps * 1000.0

    annotations: list[dict] = []
    pending_start_ms: float | None = None
    pending_failed = False
    frame_no = 0

    def seek(ms_delta: float) -> None:
        nonlocal frame_no
        frame_no = max(0, min(total_frames - 1,
                              frame_no + int(ms_delta / 1000.0 * fps)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ms = frame_no / fps * 1000.0
        status = f"f{frame_no}/{total_frames}  t={ms / 1000.0:.2f}s"
        if pending_start_ms is not None:
            color = (0, 0, 255) if pending_failed else (0, 255, 0)
            cv2.putText(frame, f"cycle open from {pending_start_ms / 1000.0:.2f}s",
                        (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
        cv2.putText(frame, status, (20, frame.shape[0] - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.imshow("annotate - SPACE start/end, f fail, w write, q quit", frame)
        key = cv2.waitKeyEx(max(1, int(1000 / fps)))

        if key == 32:  # SPACE
            if pending_start_ms is None:
                pending_start_ms = ms
                pending_failed = False
            else:
                ann = {
                    "session": args.session,
                    "cycle": len(annotations) + 1,
                    "cycle_start_ms": round(pending_start_ms, 1),
                    "cycle_end_ms": round(ms, 1),
                    "outcome": "FAILED" if pending_failed else "OK",
                    "failure_type": FAILURE_TYPES.get(
                        "", "OTHER") if pending_failed else "NONE",
                    "annotator": args.annotator,
                }
                try:
                    validate_annotation(ann, duration_ms)
                    annotations.append(ann)
                except ValueError as exc:
                    print(f"[invalid] {exc}", file=sys.stderr)
                pending_start_ms, pending_failed = None, False
        elif key in (ord("f"), ord("F")) and pending_start_ms is not None:
            pending_failed = True
        elif pending_failed and chr(key) if 0 < key < 256 else False:
            pass
        elif key == ord("u") and annotations:
            annotations.pop()
        elif key == 2490368 or key == 65361 or key == 81:  # left variants
            seek(-1000)
        elif key == 2555904 or key == 65363 or key == 83:  # right variants
            seek(1000)
        elif key in (ord("q"), 27):
            break
        elif key == ord("w"):
            out = Path(args.video).parent.parent / "annotations" / (
                f"{args.session}_{args.annotator}.jsonl")
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("a") as fh:
                for ann in annotations:
                    fh.write(json.dumps(ann, sort_keys=True) + "\n")
            print(f"wrote {len(annotations)} annotations -> {out}")
            break
        frame_no += 1

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
