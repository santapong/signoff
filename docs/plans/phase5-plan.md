# Phase 5 — Live Monitoring v0.3: Implementation Plan

Roadmap deliverable: live monitoring with <5 s end-to-end latency.

## Acceptance criteria (verified before merge)

- AC1: Live capture → detection → cycle-event broadcast latency **< 5000 ms**
  (measured, not assumed).
- AC2: A synthetic "cycle" performed in front of the camera produces a
  `cycle_completed` event on the websocket within the latency budget.
- AC3: Dashboard page (`GET /live`) loads and renders events from the
  websocket without errors.
- AC4: System survives camera loss (device unplugged) and recovers when it
  returns, without crashing the service.
- AC5: All existing tests still pass; new tests cover latency measurement,
  event emission and recovery logic.

## Scope

### 1. Live engine (`services/live_worker/engine.py`)
- Background thread: grab frames from `VideoRecorder`-style source at fps,
  feed MotionDetector, run incremental segmentation on a sliding window.
- Emits typed events to an in-process bus:
  `cycle_started`, `cycle_completed`, `fault_suspected`, plus heartbeat.
- Camera-loss watchdog: N consecutive failed grabs -> `camera_lost` event;
  attempts reconnect with backoff; emits `camera_restored`.

### 2. Event bus + API (`apps/api/live.py` mounted into apps.api.main)
- `WS /live/ws` — streams engine events as JSON lines.
- `GET /live` — minimal HTML dashboard (vanilla JS, no build step):
  shows session state, live cycle counter, last events, latency readout.
- `POST /live/start`, `POST /live/stop` — control capture device index.

### 3. Latency instrumentation
- Every frame carries monotonic capture timestamp; event payload includes
  `capture_ms`, `emit_ms`; dashboard computes e2e = emit - capture. The AC1
  test asserts this delta stays under budget across a scripted session.

### 4. Tests (`tests/test_live.py`)
- Engine against SyntheticSource: N activity windows -> N completed events
  (AC2 logic).
- Latency: capture_ms vs emit_ms deltas all < 5000 (AC1).
- Watchdog unit test with a flaky fake source -> camera_lost then
  camera_restored (AC4).
- WS endpoint integration via TestClient websocket connect receiving events.
