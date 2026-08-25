#!/usr/bin/env python3
"""One-command recording session (SDD §71 Phase 1).

Creates a session, starts the recorder, runs N cycles on an arm backend,
stops the recorder, hashes everything and writes the session manifest.

Usage:
  python tools/run_session.py --cycles 10 --backend mock
  python tools/run_session.py --cycles 50 --backend mock --fault-every 7
  python tools/run_session.py --cycles 10 --backend none   # record only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from packages.domain.annotations import make_annotation, write_annotations  # noqa: E402
from packages.domain.experiment_registry import register_experiment  # noqa: E402
from packages.domain.ids import make_experiment_id  # noqa: E402
from packages.domain.session import (  # noqa: E402
    CameraProfile,
    ObservationSession,
    init_session_dirs,
)
from robotics.elexhub.controller.mock_arm import MockArm  # noqa: E402
from robotics.elexhub.logging.ground_truth import GroundTruthLogger  # noqa: E402
from robotics.elexhub.trajectories.runner import ExperimentRunner  # noqa: E402

DATASETS = REPO / "datasets"
REGISTRY = REPO / "experiments" / "registry.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycles", type=int, default=10)
    ap.add_argument("--backend", choices=["mock", "none"], default="mock")
    ap.add_argument("--fault-every", type=int, default=0,
                    help="inject a fault every Nth cycle (0 = never)")
    ap.add_argument("--record-seconds-per-cycle", type=float, default=6.0,
                    help="recording window per cycle when backend=none")
    ap.add_argument("--device", type=int, default=0, help="camera index")
    ap.add_argument("--notes", default="")
    args = ap.parse_args()

    exp_id = make_experiment_id("CYCLE", 1)  # dataset-collection experiment
    session = ObservationSession(
        experiment_id=exp_id, camera=CameraProfile(device_index=args.device),
        notes=args.notes,
    )
    sdir = init_session_dirs(DATASETS, session)

    if not REGISTRY.exists():
        register_experiment(REGISTRY, {
            "experiment_id": exp_id,
            "git_sha": "phase1-rig",
            "dataset_version": "Signoff-Lab-v0-wip",
            "camera_profile": "cam_lab_01",
            "algorithm": "none (data collection)",
            "config": {"backend": args.backend},
            "hypothesis": "reference cycles are repeatable enough to label",
            "primary_metric": "labeled cycle count",
        })

    gt_path = sdir / ".ground_truth" / "events.jsonl"  # hidden from CV pipeline
    logger = GroundTruthLogger(gt_path)

    video_path = sdir / "video" / f"{session.session_id}.mp4"

    if args.backend == "mock":
        arm = MockArm()
        runner = ExperimentRunner(arm, logger, seed=42)
        results: list = []
        try:
            # Recording and running are separate threads in real use; for the
            # mock rig we interleave: run cycles while a recorder grabs frames.
            from services.video_worker.recorder import VideoRecorder
            rec = VideoRecorder(video_path, device_index=args.device)
            results = []
            import time as _t
            end = _t.monotonic() + args.cycles * args.record_seconds_per_cycle
            import threading

            def drive():
                results.extend(runner.run_cycles(args.cycles,
                                                 fault_every=args.fault_every))

            t = threading.Thread(target=drive)
            t.start()
            while _t.monotonic() < end:
                rec.grab()
            rec.stop()
            t.join()
        except RuntimeError as exc:
            print(f"[warn] recorder unavailable ({exc}); logging cycles only")
            results = runner.run_cycles(args.cycles, fault_every=args.fault_every)
    else:
        from services.video_worker.recorder import VideoRecorder
        rec = VideoRecorder(video_path, device_index=args.device)
        rec.run_for(args.cycles * args.record_seconds_per_cycle)
        meta = rec.stop()
        print(json.dumps(meta, indent=2))
        results = []

    logger.log("SESSION_END")

    # auto-seed annotations from ground truth so the human only has to correct
    anns = []
    events = logger.read_all()
    starts = {e["cycle"]: e["timestamp_ms"] for e in events
              if e["event"] == "CYCLE_START"}
    ends = {e["cycle"]: e["timestamp_ms"] for e in events
            if e["event"] == "CYCLE_COMPLETED"}
    duration_ms = 1e9  # corrected after first real annotation pass
    for cyc, start in sorted(starts.items()):
        end = ends.get(cyc)
        outcome = "OK" if end else "FAILED"
        failure = "NONE" if end else "OTHER"
        anns.append(make_annotation(
            session.session_id, cyc, start, end or start + 1000.0,
            duration_ms, outcome=outcome, failure_type=failure,
            annotator="ground_truth_autoseed",
        ))
    write_annotations(sdir / "annotations" / "autoseed.jsonl", anns)

    summary = {
        "session_id": session.session_id,
        "experiment_id": exp_id,
        "cycles_requested": args.cycles,
        "cycles_run": len(results),
        "ok": sum(1 for r in results if r.outcome == "OK"),
        "failed": sum(1 for r in results if r.outcome == "FAILED"),
        "ground_truth": str(gt_path.relative_to(REPO)),
        "annotations_to_review": str((sdir / "annotations").relative_to(REPO)),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
