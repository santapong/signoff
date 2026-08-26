# Signoff

Camera-first, controller-independent, evidence-first measurement of robot automation performance.

Signoff observes a robot cell through an ordinary fixed camera and converts video into
measurable operational evidence: cycle counts, cycle-time distributions, operating/idle
time, failure evidence, and human-verifiable acceptance reports — with **no PLC, MES,
SCADA, or proprietary robot-controller integration required**.

> Can a camera-only system measure robot operational performance accurately enough that
> another person can verify and trust the result?

## Core principles

1. **Evidence before intelligence** — every metric traces back to inspectable visual evidence.
2. **Measurement before explanation** — WHAT/WHEN/HOW LONG before WHY.
3. **Unknown is a valid output** — uncertain evidence becomes `UNKNOWN`, never false confidence.
4. **Human review remains possible** — model predictions and human revisions are stored separately.
5. **Controller independence** — the measurement pipeline never depends on robot telemetry.
6. **No LLM/VLM in the measurement critical path** (v0.x) — acceptance numbers are deterministic and reproducible.

## Pipeline

```
Camera / Recorded Video
    → Video Processing
    → Visual Observations
    → Temporal Event Detection
    → Robot Cell State Estimation
    → Cycle Detection
    → Performance Metrics
    → Evidence Clips
    → Human Verification
    → Acceptance Evidence Report
```

## Repository layout

```
apps/api/            FastAPI REST API
apps/web/            React + TypeScript frontend
services/            video / inference / report workers
packages/
  domain/            core domain models & schemas
  cv/                computer-vision baselines (markers, classical, detection+tracking)
  temporal/          event/state inference rules
  metrics/           cycle metrics, throughput, availability
  evidence/          clip generation & provenance hashing
  reporting/         evidence report generator
robotics/elexhub/    experiment arm driver (data generation only)
experiments/         E0–E9 experiment protocols & results
datasets/manifests/  dataset cards (raw videos are NOT committed)
infra/               docker / compose
tests/               unit + golden-video tests
docs/                SDD.md, EXPERIMENT_PROTOCOL.md, DATASET_CARD.md, MODEL_CARD.md
```

## Development phases

Status as of 2026-08-26 — all software phases complete on `develop` (83/83 tests).

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 0 | ELEXHUB arm bring-up: 50 automated cycles, safe joint limits | **BACKLOG** (hardware-dependent) |
| 1 | Dataset rig: fixed webcam, ground-truth logger, 100+ labeled cycles | ✅ software done (`feature/phase1-dataset-rig` merged); physical exit (100+ labeled cycles) blocked by Phase 0 |
| 2 | Signoff offline v0.1: upload → marker/classical detection → cycles → metrics → report | ✅ done |
| 3 | CV v0.2: detection + tracking, states, failures, human review | ✅ done |
| 4 | Live monitoring v0.3 (<5 s latency) | ✅ done — verified 1.2 ms worst latency |
| 5 | Commercial MVP | ✅ done — web UI + Docker image |
| 6 | Pilot readiness: auth, multi-annotator agreement, backup/restore, feedback, runbook | ✅ done |
| 7 | External pilot → industrial pilot | ⏳ awaiting pilot customer |

### Backlog

- **Phase 0 arm bring-up** (parked): real `SerialArm` transport for the
  Arduino Mega + ELEXHUB 6-DOF arm. Blocked on hardware bring-up and the
  firmware/serial protocol details. Everything else runs without it —
  upload/review/live flows work on recorded or synthetic video.
- Physical dataset collection (100+ labeled cycles) after Phase 0 gate.
- Merge `develop` → `main` release cut when a pilot is signed.

See [docs/SDD.md](docs/SDD.md), [docs/PILOT_RUNBOOK.md](docs/PILOT_RUNBOOK.md),
and per-phase plans in [docs/plans/](docs/plans/).

## Tech stack

Python · FastAPI · Pydantic · SQLAlchemy · PostgreSQL · OpenCV · FFmpeg · PyTorch (optional) · React/TypeScript/Vite/Tailwind

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md). All changes go through pull requests;
`main` is protected.

## License

MIT — see [LICENSE](LICENSE).
