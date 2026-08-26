# Phase 6 — Commercial MVP: Implementation Plan

Roadmap deliverable: "Commercial MVP" — one deployable product a pilot
customer can use end-to-end without touching the CLI.

## Acceptance criteria (verified before merge)

- AC1: Web UI at `/ui` lists sessions, shows status (uploaded/analyzed),
  and links to reports.
- AC2: A pilot user can do the full loop **in the browser only**:
  upload video → analyze → view Markdown report + metrics.
- AC3: Live monitoring page reachable from the UI navigation.
- AC4: Product ships as a single Docker image; `docker run` serves the API+UI,
  health endpoint responds inside the container.
- AC5: Full test suite passes; new tests cover every UI route and the
  browser-only loop (AC2 simulated via HTTP client, no JS required).

## Scope

### 1. Server-rendered web UI (`apps/api/ui.py`, Jinja2 templates)
- No Node build step (Pi-friendly): Jinja2 HTML + tiny vanilla JS/CSS.
- Pages:
  - `/ui` — session list: id, created, analyzed?, F1 if available, actions.
  - `/ui/sessions/{sid}` — detail: metrics table, report.md rendered,
    review summary, annotate form (human correction loop).
  - Upload form posting straight to existing `/sessions/upload` then redirect.
  - Nav link to `/live/`.
- All state read from `datasets/` — no extra database (MVP simplicity).

### 2. Packaging (`infra/`)
- `Dockerfile`: python3.13-slim, opencv-headless, uvicorn entrypoint,
  data dir volume mount, port 8600.
- `infra/docker-compose.yml`: service + volume, env knobs
  (SIGNOFF_DATA_DIR, PORT).
- `.dockerignore`.

### 3. Minor API additions
- `GET /sessions` — JSON list of all sessions w/ analyzed flag (backs the UI).
- Report rendering helper reused by the UI page.

### 4. Tests (`tests/test_mvp.py`)
- Every UI route returns 200 and contains expected markers (AC1).
- Browser-only loop: POST multipart upload via client → POST analyze →
  GET ui detail page contains cycle count + report heading (AC2).
- `/live/` linked from nav (AC3).
- Sessions listing endpoint correctness (AC1 backing).

## Out of scope
Auth/multi-tenancy, Postgres, object storage offload, industrial hardening
(those are Phase 7 pilot feedback items).
