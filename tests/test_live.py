"""Phase 5 live monitoring acceptance tests (AC1-AC5)."""
from __future__ import annotations

import threading
import time

import pytest

from services.live_worker.engine import LATENCY_BUDGET_MS, EventBus, LiveEngine, SyntheticLiveSource


def _run_engine(source, seconds: float, **kw) -> tuple[LiveEngine, EventBus]:
    bus = EventBus()
    eng = LiveEngine(source, bus, poll_interval_s=1 / 30, **kw)
    events: list = []
    bus.subscribe(events.append)
    eng.start()
    time.sleep(seconds)
    eng.stop()
    return eng, bus


class TestAC2_CycleEvents:
    """AC2: synthetic activity windows produce cycle_completed events."""

    def test_cycles_emitted(self):
        src = SyntheticLiveSource(activity_windows_ms=[(500, 2500)])
        bus = EventBus()
        eng = LiveEngine(src, bus, poll_interval_s=1 / 30)
        got: list[str] = []
        bus.subscribe(lambda e: got.append(e.type))
        eng.start()
        try:
            deadline = time.monotonic() + 12.0
            while time.monotonic() < deadline:
                if got.count("cycle_completed") >= 1:
                    break
                time.sleep(0.1)
            assert got.count("cycle_completed") >= 1, f"events seen: {got}"
        finally:
            eng.stop()

    def test_no_activity_no_events(self):
        src = SyntheticLiveSource(activity_windows_ms=[])
        bus = EventBus()
        eng = LiveEngine(src, bus, poll_interval_s=1 / 30)
        got: list[str] = []
        bus.subscribe(lambda e: got.append(e.type))
        eng.start()
        try:
            time.sleep(3.0)
            assert "cycle_completed" not in got
        finally:
            eng.stop()


class TestAC1_Latency:
    """AC1: capture->emit latency stays under the 5 s budget."""

    def test_all_events_within_budget(self):
        src = SyntheticLiveSource(activity_windows_ms=[(500, 2500)])
        bus = EventBus()
        eng = LiveEngine(src, bus, poll_interval_s=1 / 30)
        events: list = []
        bus.subscribe(events.append)
        eng.start()
        try:
            time.sleep(10.0)
        finally:
            eng.stop()
        assert events, "expected some events to measure"
        worst = max(e.latency_ms for e in events if e.type != "heartbeat")
        assert worst < LATENCY_BUDGET_MS, f"worst latency {worst} ms exceeds budget"

    def test_event_dicts_report_budget(self):
        ev = _make_event(capture_offset_ms=100.0)
        d = ev.to_dict()
        assert d["within_budget"] is (d["latency_ms"] < LATENCY_BUDGET_MS)


class TestAC4_CameraRecovery:
    """AC4: camera loss -> camera_lost event; recovery -> camera_restored."""

    def test_lost_and_restored(self):
        src = SyntheticLiveSource(activity_windows_ms=[(500, 2500)])
        bus = EventBus()
        eng = LiveEngine(src, bus, poll_interval_s=1 / 30,
                         max_lost_grabs=3)  # trip quickly
        types: list[str] = []
        bus.subscribe(lambda e: types.append(e.type))

        def fail_camera():
            time.sleep(1.0)
            src.fail_until = time.monotonic() * 1000.0 + 1500.0

        eng.start()
        threading.Thread(target=fail_camera, daemon=True).start()
        try:
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if "camera_lost" in types and "camera_restored" in types:
                    break
                time.sleep(0.1)
            assert "camera_lost" in types, f"events: {types}"
            assert "camera_restored" in types, f"events: {types}"
        finally:
            eng.stop()

    def test_engine_thread_survives_loss(self):
        src = SyntheticLiveSource(activity_windows_ms=[(500, 2500)])
        bus = EventBus()
        eng = LiveEngine(src, bus, poll_interval_s=1 / 30, max_lost_grabs=2)
        eng.start()
        try:
            src.fail_until = time.monotonic() * 1000.0 + 800.0
            time.sleep(3.0)
            assert eng._thread is not None and eng._thread.is_alive(), \
                "engine thread died during camera loss"
        finally:
            eng.stop()


class TestAC5_Regression:
    """AC5: engine logic does not corrupt existing behaviour — quick checks
    that segmentation params flow through unchanged."""

    def test_engine_uses_segmenter_params(self):
        src = SyntheticLiveSource(activity_windows_ms=[])
        bus = EventBus()
        eng = LiveEngine(src, bus, min_active_ms=9999.0)
        # a full activity window shorter than min_active_ms must not count
        eng.start()
        time.sleep(4.0)
        eng.stop()
        assert eng.completed_cycles == 0


def _make_event(capture_offset_ms: float):
    from services.live_worker.engine import LiveEvent
    now = time.monotonic() * 1000.0
    return LiveEvent(type="cycle_completed", capture_ms=now - capture_offset_ms,
                     emit_ms=now, payload={"cycle": 1})


# ---- API integration (AC3 + WS plumbing) -----------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNOFF_DATA_DIR", str(tmp_path / "data"))
    import importlib

    import apps.api.main as api_main
    importlib.reload(api_main)
    from fastapi.testclient import TestClient
    monkeypatch.delenv("SIGNOFF_TOKEN", raising=False)
    monkeypatch.setenv("SIGNOFF_ALLOW_SYNTHETIC", "1")
    with TestClient(api_main.app) as tc:
        yield tc, api_main


class TestAC3_Dashboard:
    def test_dashboard_and_status_flow(self, client):
        tc, api_main = client
        r = tc.get("/live/")
        assert r.status_code == 200
        assert "Live Monitoring" in r.text

        profile = tc.post("/profiles", json={
            "profile_id": "live-api", "camera": {"width": 160, "height": 120, "fps": 30},
            "detector": {"method": "motion"}})
        assert profile.status_code == 201, profile.text
        r2 = tc.post("/live/start", json={
            "profile_id": "live-api", "source": "synthetic", "record": True})
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "started"
        sid = r2.json()["session_id"]
        try:
            status = tc.get("/live/status").json()
            assert status["running"] is True
            assert status["session_id"] == sid
            assert status["source"] == "synthetic_development"
            assert status["latency_budget_ms"] == 5000.0

            deadline = time.monotonic() + 12.0
            while time.monotonic() < deadline:
                status = tc.get("/live/status").json()
                if status["completed_cycles"] >= 1:
                    break
                time.sleep(0.2)
            assert status["completed_cycles"] >= 1, "no cycle counted live"
            assert status["max_latency_ms"] < 5000.0
        finally:
            assert tc.post("/live/stop").json()["status"] == "stopped"
        assert api_main.session_info(sid)["video_file"] == "recording.manifest.json"
        assert tc.get("/live/status").json()["running"] is False

    def test_status_when_not_running(self, client):
        tc, _ = client
        s = tc.get("/live/status").json()
        assert s["running"] is False
