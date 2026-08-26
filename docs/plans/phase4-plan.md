# Phase 4 — CV v0.2: Tracking, States, Failure Typing, Human Review

Roadmap deliverable extended: beyond cycle counting, classify *what happened*
in each cycle and let a human confirm/correct results before they are trusted.

## Scope of this build

### 1. Centroid tracker (`packages/cv/signoff_cv/tracking.py`)
- Deterministic nearest-centroid tracker over binarized motion blobs
  (no randomness). Tracks have id, birth/death ms, last position.
- Output per frame: list of `TrackedBlob(id, cx, cy, area)`.

### 2. Cycle phase classifier (`packages/cv/signoff_cv/states.py`)
- Splits each detected cycle into phases from the activity profile:
  APPROACH (rising), GRASP (plateau start), TRANSFER (mid plateau),
  PLACE/RETRACT (falling) using relative-duration cuts. Best-effort,
  explicitly marked INFERRED (protocol rule).

### 3. Failure typing (`packages/cv/signoff_cv/failures.py`)
- Heuristic mapping from phase profile + ground-truth fault labels when
  available: missing GRASP plateau -> GRASP_MISS candidate; abrupt end ->
  INTERRUPTED; activity after place without return -> OBJECT_SLIP candidate.
- Output: per-cycle `FailureHypothesis(type, confidence, evidence)` — always
  hypotheses, never asserted facts (E8 agreement study input).

### 4. Human review (`apps/api` additions)
- `GET /sessions/{sid}/review` — cycles with auto outcome/failure hypothesis
  and their confidence, flagged `needs_review` when confidence < threshold.
- `POST /sessions/{sid}/review` — body: accepted/corrected outcomes; appends
  to `annotations/<sid>_reviewer.jsonl` via the domain schema (validated).
- Reviewed cycles are recorded with annotator id; never merged into
  predictions in place (SDD §26).

### 5. Tests (`tests/test_cv2.py`)
- Tracker: two crossing blobs keep distinct ids; deterministic across runs.
- States: synthetic cycle splits into expected phase order.
- Failures: grasp-miss-shaped profile -> GRASP_MISS hypothesis.
- Review API roundtrip: upload->analyze->review listing->submit correction->
  annotation file validated & written.

## Out of scope
ML classifiers, multi-camera, live mode (<5 s latency is next phase).
