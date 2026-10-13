# Signoff current milestone status

Checked 13 October 2026. Implementation is on `feat/camera-program-v02`, tracked by [issue 1](https://github.com/santapong/signoff/issues/1). The v0.2.0-rc1 software candidate is built from the camera-first program.

| Milestone | Software state | Required external evidence |
|---|---|---|
| M0 | Requirements and qualification contract frozen | Actual station/profile confirmation before physical scoring |
| M1 | Replay and metric defects repaired; locked environment and lint clean | None for software regression |
| M2 | Timestamped USB/file adapters, one capture owner and lossless segmented recording implemented | Actual camera capture/observed rate pending |
| M3 | Shared marker/motion engine, finalized stable events, cumulative totals and replay parity implemented | Real-station accuracy pending |
| M4 | Durable analysis, playable evidence, revisions, exports and owner decision workflow implemented | Actual owner-pilot operation pending |
| M5 | Frozen-dataset and qualification tooling implemented and tested | No real recordings yet; physical qualification pending |
| M6 | Authentication, packaging, safe restore, browser workflow and release gate implemented | OWNER pilot acceptance and real-camera soak pending |
| M7 | Optional frozen-report comparison CLI implemented and tested | Comparisons remain provisional until physical/pilot gates pass |

Fresh validation: **199 integrated tests plus 13 focused headless-reference tests passed**, Ruff clean. A disposable browser rehearsal uploaded a generated marker video, detected four cycles, played derived evidence and submitted an UNKNOWN review. This is software/simulation evidence. The owner reported no real recordings and selected a pilot with assistant support; no independent external pilot is claimed.

The production release gate remains PENDING. The release-candidate package is for software rehearsal and capture preparation; it is not a physically qualified instrument release.

## Historical software checkpoint from August

The following is preserved as reported history. Its pilot-ready wording does not override the October scope and pending physical gates above.

# August project checkpoint

Last updated: 2026-08-26

## Where the project stands

All seven software phases are complete and merged to `develop`
(83/83 tests passing). The system is pilot-ready: a customer can install via
Docker, upload videos, get camera-only cycle detection with metrics/reports,
do human review, run live monitoring under 5 s latency (measured ~1 ms), and
submit feedback — all through the browser.

## Completed phases

| Phase | What was built |
|-------|----------------|
| 1 | Dataset rig: session/experiment IDs, hidden ground-truth logger, scripted cycle runner with fault injection, mock + gated serial arm drivers, webcam recorder (sha256 + frame clock), annotation schema & CLI |
| 2 | CV v0.1: deterministic motion signal, hysteresis cycle segmenter, idle detection, E2 evaluation (P/R/F1 vs ground truth) |
| 3 | Offline pipeline: metrics engine, Markdown/JSON reports, FastAPI upload→analyze→report |
| 4 | CV v0.2: centroid tracking, phase splitting, failure hypotheses (INFERRED), human review API with per-annotator storage |
| 5 | Live monitoring: threaded engine, event bus, WebSocket stream, dashboard; AC verified at 1.2 ms worst latency (budget 5 s) |
| 6 | Commercial MVP: server-rendered web UI (sessions/upload/report), Docker image + compose |
| 7 | Pilot readiness: token auth, multi-annotator kappa agreement (E8), backup/restore with sha256 manifest, feedback channel, pilot runbook |

## Backlog

### Phase 0 — ELEXHUB arm bring-up (parked)

Real `SerialArm` transport for the Arduino Mega + ELEXHUB 6-DOF arm.

**Why parked:** hardware-dependent. Requires physical bring-up and the
firmware/serial protocol details before any software-defined joint limit can
be trusted (SDD §7 safety gate).

**Unblock steps when resumed:**
1. Connect Mega to Pi via USB; identify the port (`ls /dev/ttyUSB* /dev/ttyACM*`).
2. Provide firmware source or protocol (e.g. `#<ch>P<pw>T<time>` style).
3. Fill `hardware_profile.yaml` (verified joint limits, speed cap) — gate
   refuses UNKNOWN fields by design.
4. Implement transport in `robotics/elexhub/controller/serial_arm.py`.
5. Phase 0 exit: 50 safe automated cycles → unlocks physical dataset
   collection (Phase 1 physical exit: 100+ labeled cycles).

### Other backlog items

- Physical dataset collection (after Phase 0)
- `develop` → `main` release cut when a pilot is signed
- TLS termination in front of the container (reverse proxy) for external pilots

## Key artifacts

- Plans: `docs/plans/phase{1..7}-plan.md`
- Runbook: `docs/PILOT_RUNBOOK.md`
- Experiments: `docs/EXPERIMENT_PROTOCOL.md` (E0–E9)
- Deploy: `infra/Dockerfile`, `infra/docker-compose.yml`
