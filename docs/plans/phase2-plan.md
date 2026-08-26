# Phase 2 — Vision Pipeline: Implementation Plan

SDD goal (E2): camera-only cycle detection with >=95% F1 against the hidden
ground truth from Phase 1. Pure OpenCV first — no ML until the E9 no-ML
baseline is measured (protocol rule: baseline before any AI claim).

## Scope of this build

### 1. Frame source (`packages/cv/signoff_cv/source.py`)
- `FrameSource` abstraction: webcam (OpenCV V4L2) and `VideoFile` replay.
- Replay yields `(frame, ms)` using `frame_index.jsonl` when present so
  detection runs on exactly the recorded clock.

### 2. Scene change signal (`packages/cv/signoff_cv/motion.py`)
- Per-frame diff metrics on a fixed ROI list: mean abs diff, edge density delta,
  histogram correlation vs a rolling background model (MOG2-free, deterministic).
- Output: `MotionSignal(frame_no, ms, roi_scores dict)`.

### 3. Cycle segmenter (`packages/cv/signoff_cv/segmenter.py`)
- Convert motion signal into cycle boundaries: hysteresis threshold +
  minimum-duration + refractory window -> `DetectedCycle(start_ms, end_ms)`.
- Idle detection: sustained below-threshold stretches -> idle episodes.

### 4. Evaluation (`packages/cv/signoff_cv/evaluate.py`)
- Match detected cycles to ground-truth CYCLE_START/COMPLETED events with
  tolerance (default +-500 ms). Produce precision/recall/F1 per session,
  written as JSON to `datasets/sessions/<sid>/vision_report.json`.
- This is the E2 instrument; also used for E3/E4/E6 degradation runs.

### 5. CLI (`tools/run_vision.py`)
- `run_vision.py <session_dir> [--tolerance-ms N] [--device N]`
- Live mode: score incoming frames against a running segmentation for smoke
  tests without hardware cycles.

## Tests (`tests/test_cv.py`)
- Synthetic frame generator (moving square = activity, static = idle):
  - segmenter finds exact N synthetic cycles;
  - tolerance matching: perfect run -> F1=1.0; shifted detections degrade
    predictably; missed/false cycles reflected in P/R;
  - motion signal determinism (same frames -> same scores);
  - idle episode boundaries detected.

## Out of scope (later)
ML models, lighting-condition training, multi-camera fusion, gripper-state
classification (failure typing comes with E7 analysis).

## Exit criteria mapping
- E2 harness runnable on any Phase 1 session directory, offline, deterministic.
- Physical exit (>=95% F1 on 100+ labeled cycles) after Phase 0 gate bring-up.
