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

| Phase | Deliverable |
|-------|-------------|
| 0 | ELEXHUB arm bring-up: 50 automated cycles, safe joint limits |
| 1 | Dataset rig: fixed webcam, ground-truth logger, 100+ labeled cycles |
| 2 | Signoff offline v0.1: upload → marker/classical detection → cycles → metrics → report |
| 3 | CV v0.2: detection + tracking, states, failures, human review |
| 4 | Live monitoring v0.3 (<5 s latency) |
| 5 | Commercial MVP |
| 6–7 | External pilot → industrial pilot |

See [docs/SDD.md](docs/SDD.md) for the full Software Design Document.

## Tech stack

Python · FastAPI · Pydantic · SQLAlchemy · PostgreSQL · OpenCV · FFmpeg · PyTorch (optional) · React/TypeScript/Vite/Tailwind

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md). All changes go through pull requests;
`main` is protected.

## License

MIT — see [LICENSE](LICENSE).
