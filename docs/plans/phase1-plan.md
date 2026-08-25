# Phase 1 — Dataset Rig: Implementation Plan

SDD §71 Phase 1 deliverables: fixed webcam, consistent scene, ground-truth logger,
video recording, experiment IDs, manual annotations. Exit: 100+ labeled cycles.
Hardware (ELEXHUB arm + USB webcam) is physically available; Phase 0 bring-up
proceeds alongside but is gated separately (50 safe cycles).

## Scope of this build (software only)

All code lands under existing SDD §34 layout. No CV yet — that's Phase 2.

### 1. Session / Experiment identity (`packages/domain/`)
- `ids.py`: ULID-style sortable IDs (`ses_`, `exp_`, `cyc_` prefixes).
- `session.py`: `ObservationSession` dataclass — session_id, experiment_id,
  camera_profile, started_at, notes; JSON serialization to
  `datasets/sessions/<session_id>/session.json`.
- `experiment_registry.py`: append-only YAML/JSON registry for EXP entries
  (SDD §89 schema) at `experiments/registry.jsonl`.

### 2. Hidden ground-truth logger (`robotics/elexhub/logging/`)
- `ground_truth.py`: JSONL event logger writing to a file the vision pipeline
  must never read during evaluation (SDD §40). Events: CYCLE_START,
  CYCLE_COMPLETED, GRIPPER_CLOSED/OPENED, FAULT_INJECTED with monotonic
  timestamps (ms since session epoch) + wall-clock ISO.
- `runner.py`: scripted reference-cycle runner (SDD §9 state machine:
  HOME→APPROACH_SOURCE→…→HOME) driving an abstract `RobotArm` driver;
  logs ground truth automatically; N cycles, fault injection hooks
  (skip grasp, drop object, interrupt mid-cycle) for E7 data.

### 3. Arm driver interface (`robotics/elexhub/controller/`)
- `base.py`: abstract `RobotArm` per SDD §39 (home/move_joint/move_pose/
  open_gripper/close_gripper/emergency_stop) + joint-limit enforcement hook
  (SAF-002) and speed cap (SAF-003).
- `mock_arm.py`: deterministic mock arm for testing the whole rig without hardware.
- `serial_arm.py`: skeleton serial driver with hardware discovery gate — refuses
  to run until `hardware_profile.yaml` has no UNKNOWN fields (SDD §7).

### 4. Video recorder (`services/video_worker/recorder.py`)
- OpenCV V4L2 capture → MP4 (H.264 via ffmpeg pipe or mp4v fallback), fixed fps,
- On stop: compute SHA-256 (SDD §20), extract metadata (duration/fps/resolution),
  write `<video>.meta.json` next to file under `datasets/sessions/<sid>/video/`.
- Frame timestamps logged to `frame_index.jsonl` (frame_no → ms) so annotations
  and ground truth share one clock.

### 5. Annotation schema + tooling (`packages/domain/annotations.py`, `tools/`)
- Schema per SDD §44: session, cycle index, start_ms/end_ms, outcome, failure_type,
  annotator id. JSONL files: `annotations/<session>_annotator.jsonl`.
- Validation function (start<end, within video duration, enum checks).
- `tools/annotate_cli.py`: keyboard-driven OpenCV playback — SPACE mark cycle
  start/end, f=fail flag, arrows scrub, w=write. Writes JSONL directly.
- `tools/clock_sync_check.py`: compares frame-index clock vs ground-truth clock drift.

### 6. Orchestration CLI (`tools/run_session.py`)
- One command per recording session: create session ID → start recorder →
  run N cycles (mock or real arm) → stop → hash → write session manifest +
  ground truth (hidden) → print annotation TODO count.

## Tests (`tests/`)
- ids uniqueness/ordering; ground-truth logger ordering & monotonic time;
- runner produces exactly N CYCLE_START/N COMPLETED pairs incl. injected faults;
- joint-limit & speed-cap enforcement raises;
- recorder roundtrip on synthetic frames (mp4v), hash determinism;
- annotation validation happy/edge cases; registry append-only behavior.

## Out of scope (later phases)
Real servo comms beyond skeleton, CV detection, metrics engine, API/web.

## Exit criteria mapping
- Recorder + logger + annotations working end-to-end on mock arm in CI-like tests.
- Physical exit (100+ labeled cycles) happens after Phase 0 gate: 50 safe cycles.
