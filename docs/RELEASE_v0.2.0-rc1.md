# Signoff v0.2.0 release candidate

This package implements the camera-first software program. **Release channel: candidate.** There are no real station recordings or accepted owner-pilot receipts yet, so production eligibility remains PENDING.

## Delivered software

Immutable station profiles; explicit USB/file input; strict decoded/captured clocks; lossless segmented recording; shared marker/motion processing; finalized events and cumulative counts; truthful unavailable/UNKNOWN metrics; durable analysis jobs; hashed playable cycle evidence; append-only reviews; JSON/CSV/HTML/Markdown and printable exports; owner acceptance; safe restore; optional experiment comparison.

## Verification

The integrated host suite passed 199 tests, followed by 13 focused headless-reference tests, with Ruff clean. A generated four-cycle video exercised the visible browser profile/upload/analyze/evidence/review flow. Tests include recorded/live parity, clock and visibility gaps, duplicate/frozen dataset inputs, independent annotation lineage, report integrity, owner receipt binding, HTTP/WebSocket authentication and staged restore.

These checks do not establish physical robot accuracy. A real-camera two-hour soak, station qualification and owner operation remain pending. Exact environment/build/browser receipts accompany the candidate under `docs/validation/2026-10-13/`.

## Production eligibility

1. Collect and independently annotate real station recordings using CAPTURE_GUIDE.md.
2. Freeze protocol, profile, session splits and every media/clock/reference hash.
3. Run `tools/qualify_dataset.py` and inspect per-session scores and unsupported conditions.
4. Complete an OWNER pilot decision after inspecting the qualified report. Bind report_hash, report file SHA-256, qualification SHA-256 and manifest SHA-256.
5. Run `tools/release_gate.py`; PENDING is a concrete blocker, not approval.
6. Follow the existing PR flow into develop/main. Only then tag the qualified release from main and publish production artifacts.

The read-only release gate never merges, tags or deploys. This candidate introduces no robot command, cloud training, Pi operation or public service exposure.

## Recovery

Interrupted capture/job intervals remain explicit. Rerunning analysis creates a new immutable run. Restore validates archive members and hashes in a new empty destination; prior evidence is preserved. Rollback uses the previous software package while retaining the versioned evidence readers.
