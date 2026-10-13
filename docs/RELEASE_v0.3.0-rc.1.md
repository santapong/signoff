# Signoff v0.3.0 release candidate

The v0.3.0-rc.1 software candidate implements the owner-selected Evidence Workbench. It is ready for software rehearsal and camera commissioning. Production eligibility remains PENDING until actual station qualification, live soak and the owner pilot are complete.

## Delivered workflow

- A navy navigation rail and light/violet workbench: source video, selectable cycles, source-clock timeline, outcome inspector, compact measurements and expandable evidence.
- Named stations, visual normalized task regions, camera metadata and explicit preview-only operation. Recording reuses the same capture owner and freezes its runtime configuration; stop preserves the archive and releases ownership.
- Recorded/live sessions, explicit source declarations, recovered analysis status, matching-job reuse, immutable review notes and refreshed reports.
- A separate blinded original-frame reference editor with authoritative clocks, disclosed reviewer kinds, immutable cycle/inactivity revisions and human adjudication.
- Structured qualification datasets, immutable freezes, bounded persistent scoring, readable cycle/inactivity results, actual-observation soak receipts and selected owner evidence bindings.
- Locked runtime dependencies, a pinned wheel backend, source-bound container builds, candidate archives and backward readers for existing evidence. Source identity remains unavailable rather than inheriting an unrelated parent Git checkout.

## Verification scope

The integrated local suite passed 266 tests before the final empty-choice and packaged-provenance regressions. Nine affected regressions then passed, including two new empty-choice cases and two new source-root cases. Those scopes overlap; they are not added as a unique total. Ruff and JavaScript syntax checks are clean. The final candidate has 270 unique test cases; remote CI results and final build identities are recorded separately.

The visible browser exercised synthetic preview, region dragging and ordinary profile fields; clearly labeled simulation upload and asynchronous analysis; reload recovery; four-cycle timeline, playable H264 evidence and UNKNOWN review notes; stale-report warning and refreshed reports; original source-frame navigation; empty/draft qualification state; and simulated recording/stop/preservation. Responsive and container checks are retained in the dated software receipt when executed.

These are software and simulation results. No real USB capture or physical robot qualification is established by this receipt. Large-recording reference indexing performance requires the real pilot. Owner acceptance is not inferred from the selected design or an assistant-operated rehearsal.

## Production release requirements

Collect and independently annotate the real station dataset described in CAMERA_FIRST_PLAN.md. Preserve the 6/2/2 nominal split with at least 360/120/120 cycles, at least 100 changed-condition cycles and 100 held-out inactivity episodes. The actual two-hour soak must bind source media, clocks, profile, code, observation counts and published rate/latency targets.

The owner reviews the inactivity results and records an ACCEPT decision against the exact frozen report, manifest and qualification receipt. The read-only release gate must return READY. Then follow the existing PR/approval flow to main; tags are created from main. Until then, distribute this software as a candidate, preserving its unqualified/provisional labels.

## Install and rollback

Use README's local/container setup and a private operator token. Camera access begins only after explicit selection and opening preview; recording is separate. Archived ZIP/wheel installations use the manifest's SIGNOFF_GIT_SHA. Keep the preceding candidate and prior frozen evidence. A rollback changes software, not existing reports, references, recordings or decisions.
