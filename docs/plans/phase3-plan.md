# Phase 3 — Signoff Offline v0.1: Implementation Plan

Roadmap deliverable: "upload → marker/classical detection → cycles →
metrics → report". Everything runs locally against Phase 2's CV package;
no ML (E9 no-ML baseline rule).

## Scope of this build

### 1. Metrics engine (`packages/metrics/signoff_metrics.py`)
- Per-cycle metrics from ground truth + annotations + detections:
  cycle duration stats (mean/p50/p95), success rate, failure breakdown by
  failure_type, throughput (cycles/min), idle ratio.
- Detection quality metrics re-use Phase 2 MatchReport.
- Pure functions over JSONL/JSON inputs — no I/O beyond reading files passed in.

### 2. Report generator (`packages/reporting/signoff_report.py`)
- Renders a session report to Markdown (`reports/<sid>/report.md`) plus
  `metrics.json` machine copy. Sections: session header, cycle table,
  outcome summary, detection F1, anomalies (fault cycles), data integrity
  (video sha256 from meta).

### 3. API service (`apps/api/`)
- FastAPI app:
  - `POST /sessions/upload` — multipart video upload -> creates
    `datasets/sessions/<new_sid>/video/<name>.mp4`, computes sha256 +
    frame index via recorder helpers, writes session.json manifest.
  - `POST /sessions/{sid}/analyze` — runs Phase 2 segmentation + evaluation
    + metrics + report; returns job result inline (offline = synchronous).
  - `GET /sessions/{sid}/report` — Markdown report text.
  - `GET /health`.
- Storage root configurable via env `SIGNOFF_DATA_DIR` (default ./datasets).

### 4. Tests (`tests/test_pipeline.py`)
- Metrics math on fixture inputs (durations, failure breakdown, throughput).
- Report contains required sections & numbers match metrics.json.
- API end-to-end with synthetic video file: upload -> analyze -> report 200s,
  correct F1 plumbing; sha256 recorded matches uploaded bytes.

## Out of scope
Web UI (next), live monitoring (<5 s), tracking/failure typing, ML.
