# Phase 7 — Pilot Readiness: Implementation Plan

Roadmap deliverable: "External pilot → industrial pilot". This phase makes
the MVP safe to hand to a real pilot customer and to collect feedback that
survives contact with production reality.

## Acceptance criteria (verified before merge)

- AC1: **Auth** — UI and API require a bearer token / login; health endpoint
  stays open for monitoring. Wrong/missing token -> 401.
- AC2: **Multi-user annotations** — two annotators can submit reviews for the
  same session; both annotation files persist separately; agreement (E8)
  computable from stored data.
- AC3: **Backup & restore** — a pilot's dataset tree can be archived to a
  single tar.gz and restored byte-identical (sha256 verified).
- AC4: **Feedback channel** — every session page has a feedback form; entries
  land in `datasets/feedback/<sid>.jsonl` with timestamp + session id.
- AC5: **Pilot runbook exists** (`docs/PILOT_RUNBOOK.md`) covering install,
  first session, review loop, backup, escalation.
- AC6: Full test suite passes including new tests for each criterion above.

## Scope

### 1. Auth (`apps/api/auth.py`)
- Single shared pilot token via env `SIGNOFF_TOKEN`; if unset, auth disabled
  (dev mode) so local tests stay simple.
- FastAPI dependency: checks `Authorization: Bearer <token>` on all routers
  except `/health`. UI uses cookie set at `/ui/login`.

### 2. Annotation agreement (E8 instrument, `packages/domain/agreement.py`)
- Cohen's kappa between two annotators' outcome labels per session.

### 3. Backup tooling (`tools/pilot_backup.py`)
- `backup` -> `datasets_backup_<ts>.tar.gz`; `restore <archive>` verifies
  sha256 manifest of every file after extraction.

### 4. Feedback (`apps/api/ui.py`)
- `POST /ui/sessions/{sid}/feedback` — appends JSONL: ts, sid, author,
  rating 1-5, comment.

### 5. Runbook (`docs/PILOT_RUNBOOK.md`)

## Out of scope
Multi-tenant isolation, TLS termination (reverse proxy handles it), billing.
