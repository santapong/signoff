# Signoff current milestone status

## Maintenance scope on 14 October 2026

The owner requests maintenance only: fixes, measured optimization and architecture
updates, with no new product features. [Issue #5](https://github.com/santapong/signoff/issues/5)
tracks this bounded pass. The existing Evidence Workbench candidate is preserved;
real station qualification remains pending under issue #3. See the implemented
[runtime architecture](ARCHITECTURE.md) for module boundaries and lifecycle/data
invariants. Maintenance test and benchmark results are recorded separately from
physical acceptance.

The bounded maintenance change adds eight-frame adjacent JPEG reuse while
retaining source/clock/index integrity checks, guarantees all owned worker cleanup
on exceptional lifecycle exits, and releases failed-recovery process locks.
The [simulation benchmark](validation/2026-10-14/reference-maintenance.json) reduced
decoded work by 66.6% and median three-frame navigation time from 9.27 s to 2.76 s
on the declared low-resolution fixture. Cold/random late-frame decoding remains
linear. Prior candidate artifacts and frozen evidence remain unchanged.

## Evidence Workbench continuation on 14 October 2026

The owner selected the Evidence Workbench direction and authorized the v0.3 program under [issue #3](https://github.com/santapong/signoff/issues/3). G0–G5 software is implemented: visual stations and explicit preview/capture, session job recovery and review, original-frame references, structured qualification and exact owner evidence bindings. The v0.3.0-rc.1 candidate is for rehearsal and commissioning.

The integrated local suite passed 266 tests, followed by nine affected regressions (four new unique cases). Browser checks exercised simulation upload/review, original-frame navigation, visual profile controls and synthetic recording preservation. Final package/CI receipts remain separate from these source checks. G6 actual station qualification, real USB test, two-hour soak, owner acceptance and main release approval remain pending. See [v0.3 release notes](RELEASE_v0.3.0-rc.1.md).

## Historical v0.2 candidate checkpoint

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

Fresh validation: **199 integrated tests plus 13 focused headless-reference tests passed**, followed by a **44-case provenance/vision/workflow regression** after the container provenance repair. Ruff is clean. A disposable browser rehearsal uploaded a generated marker video, detected four cycles, played derived evidence and submitted an UNKNOWN review. This is software/simulation evidence. The owner reported no real recordings and selected a pilot with assistant support; no independent external pilot is claimed.

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
