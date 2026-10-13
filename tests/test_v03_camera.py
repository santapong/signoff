"""Preview/recording ownership and actual-observation receipts without hardware."""

from __future__ import annotations

import importlib
import json
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from packages.cv.signoff_cv.pipeline import analyze_video
from packages.domain.profiles import validate_profile
from services.live_worker.camera import DeadlinePacer, discover_cameras
from services.live_worker.engine import EventBus, LiveEngine
from services.live_worker.soak import SoakSamples, canonical_hash


def marker_image(region="HOME"):
    image = np.full((240, 320, 3), 255, np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker, x, y in (
        (0, 220, 20),
        (
            1,
            *{"HOME": (35, 30), "PICK": (35, 165), "PLACE": (230, 165), "OUTSIDE": (140, 95)}[
                region
            ],
        ),
    ):
        image[y : y + 40, x : x + 40] = cv2.aruco.generateImageMarker(dictionary, marker, 40)[
            :, :, None
        ]
    return image


class FakeCamera:
    """Generated observations stand in for a server USB source; never a device."""

    def __init__(self, device=2, synthetic=False, fps=30):
        self.device_index = None if synthetic else device
        self.synthetic = synthetic
        self.requested = {"width": 1280, "height": 720, "fps": fps}
        self.negotiated = {"width": 320, "height": 240, "fps": fps}
        self.reads, self.closed = 0, False
        self.regions = (
            ["HOME"] * 10 + ["OUTSIDE"] * 4 + ["PICK"] * 6 + ["PLACE"] * 6 + ["HOME"] * 12
        )

    def read(self):
        self.reads += 1
        return True, marker_image(self.regions[(self.reads - 1) % len(self.regions)])

    def close(self):
        self.closed = True


def station(width=320, device=2, fps=30):
    return validate_profile(
        {
            "profile_id": f"station-{width}",
            "camera": {"device_index": device, "width": width, "height": 240, "fps": fps},
            "detector": {
                "method": "aruco",
                "home": [0.05, 0.05, 0.25, 0.3],
                "pick": [0.05, 0.6, 0.25, 0.3],
                "place": [0.65, 0.6, 0.25, 0.3],
            },
        }
    )


@pytest.fixture()
def camera_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNOFF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("SIGNOFF_TOKEN", raising=False)
    monkeypatch.delenv("SIGNOFF_REQUIRE_AUTH", raising=False)
    monkeypatch.setenv("SIGNOFF_ALLOW_SYNTHETIC", "1")
    import apps.api.live as live
    import apps.api.main as main

    importlib.reload(main)
    if live.get_engine():
        live._stop_engine()
    live._state.update(engine=None, session_id=None, session_dir=None, bus=None)
    sources = []

    def factory(device_index, synthetic=False, **settings):
        source = FakeCamera(device_index, synthetic, settings.get("fps", 30))
        sources.append(source)
        return source

    monkeypatch.setattr(live, "make_source", factory)
    monkeypatch.setattr(
        live,
        "discover_cameras",
        lambda: [{"device_index": 2, "name": "Mock USB", "path": "/dev/video2"}],
    )
    with TestClient(main.app) as client:
        yield client, main, live, sources


def wait_frame(client):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        state = client.get("/live/status").json()
        if state.get("readiness", {}).get("frames_received"):
            return state
        time.sleep(0.01)
    raise AssertionError("mock preview did not publish a frame")


def test_named_discovery_reads_only_metadata(tmp_path, monkeypatch):
    sys_root, dev_root = tmp_path / "sys", tmp_path / "dev"
    sys_root.mkdir()
    dev_root.mkdir()
    for number, name in ((12, "Metadata node"), (2, "USB Webcam")):
        node = sys_root / f"video{number}"
        node.mkdir()
        (node / "name").write_text(name)
        (dev_root / node.name).touch()
    (sys_root / "video-invalid").mkdir()
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: pytest.fail("discovery opened a camera"))
    result = discover_cameras(sys_root, dev_root)
    assert [r["device_index"] for r in result] == [2, 12]
    assert result[0]["name"] == "USB Webcam"
    assert all(r["capture_capability"] == "unverified" for r in result)


def test_closed_status_discovery_navigation_never_start_camera(camera_client):
    client, main, live, sources = camera_client
    assert client.get("/live/cameras").json()["probing_required"] is True
    assert client.get("/live/status").json()["mode"] == "closed"
    assert client.get("/live/preview").status_code == 404
    assert client.get("/ui/profiles").status_code == 200
    assert sources == [] and live.get_engine() is None


def test_preview_opens_once_without_session_archive_and_stop_releases(camera_client):
    client, main, live, sources = camera_client
    opened = client.post(
        "/live/preview/start",
        json={"source": "usb", "device_index": 2, "width": 1280, "height": 720, "fps": 30},
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["mode"] == "preview" and opened.json()["session_id"] is None
    state = wait_frame(client)
    assert state["actual_camera"]["width"] == 320
    assert state["actual_camera"]["height"] == 240
    assert state["readiness"]["markers"]["detected_ids"] == [0, 1]
    assert state["recording_state"] == "disabled"
    assert not list((main.DATA_DIR / "sessions").glob("*/session.json"))
    assert client.get("/live/preview").content[:2] == b"\xff\xd8"
    assert (
        client.post("/live/preview/start", json={"source": "usb", "device_index": 2}).status_code
        == 409
    )
    assert len(sources) == 1
    assert client.post("/live/preview/stop").json()["mode"] == "closed"
    assert sources[0].closed and live.get_engine() is None


def test_preview_to_recording_reuses_owner_and_freezes_actual_profile(camera_client):
    client, main, live, sources = camera_client
    profile = station()
    assert client.post("/profiles", json=profile).status_code == 201
    assert (
        client.post("/live/preview/start", json={"source": "usb", "device_index": 2}).status_code
        == 200
    )
    wait_frame(client)
    engine = live.get_engine()
    started = client.post(
        "/live/start",
        json={
            "source": "usb",
            "device_index": 2,
            "profile_id": profile["profile_id"],
            "profile_revision": profile["revision"],
            "record": True,
            "source_kind": "manual_tabletop_rehearsal",
            "session_name": "Mock capture",
        },
    )
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]
    assert started.json()["runtime_profile"]["actual_resolution"] == [320, 240]
    assert started.json()["profile_hash"] == profile["profile_hash"]
    assert live.get_engine() is engine and len(sources) == 1
    assert client.post("/live/preview/stop").status_code == 409
    assert (
        client.post(
            "/live/start",
            json={
                "source": "usb",
                "device_index": 2,
                "profile_id": profile["profile_id"],
                "record": True,
            },
        ).status_code
        == 409
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if client.get("/live/status").json()["coverage"]["observation_count"] >= 2:
            break
        time.sleep(0.02)
    assert engine.pipeline.observation_count >= 2
    stopped = client.post("/live/stop")
    assert stopped.status_code == 200 and stopped.json()["soak_receipt_available"] is True
    session_dir = main.DATA_DIR / "sessions" / sid
    session = json.loads((session_dir / "session.json").read_text())
    assert session["name"] == "Mock capture" and session["physical_capture_verified"] is False
    capture = json.loads((session_dir / "capture-profile.json").read_text())
    assert capture["clock_origin"] == "recording_start"
    assert capture["profile_hash"] == profile["profile_hash"]
    receipt = json.loads((session_dir / "live-soak.json").read_text())
    assert receipt["duration_ms"] < 7200000
    assert receipt["source_kind"] == "manual_tabletop_rehearsal"
    assert receipt["latency"]["sample_count"] == receipt["observation_count"] > 0
    assert receipt["recording"]["integrity_verified"] is True
    assert receipt["receipt_hash"] == canonical_hash(receipt)
    manifest = session_dir / "video" / "recording.manifest.json"
    assert receipt["duration_ms"] == json.loads(manifest.read_text())["end_ms"]
    replay = analyze_video(manifest, profile)
    assert replay["completed_cycles"] == engine.completed_cycles
    assert sources[0].closed


def test_changed_source_or_wrong_resolution_keeps_preview_owner(camera_client):
    client, main, live, sources = camera_client
    profile = station(width=640)
    client.post("/profiles", json=profile)
    client.post("/live/preview/start", json={"source": "usb", "device_index": 2})
    wait_frame(client)
    wrong = client.post(
        "/live/start",
        json={
            "source": "usb",
            "device_index": 2,
            "profile_id": profile["profile_id"],
            "record": True,
        },
    )
    assert wrong.status_code == 422 and "resolution" in wrong.text.lower()
    assert live.get_engine().mode == "preview" and live.get_engine().recorder is None
    assert len(sources) == 1
    assert (
        client.post("/live/preview/start", json={"source": "usb", "device_index": 3}).status_code
        == 409
    )


def test_synthetic_requires_opt_in_and_cannot_claim_physical(camera_client, monkeypatch):
    client, main, live, sources = camera_client
    monkeypatch.delenv("SIGNOFF_ALLOW_SYNTHETIC")
    assert client.post("/live/preview/start", json={"source": "synthetic"}).status_code == 403
    assert not sources
    monkeypatch.setenv("SIGNOFF_ALLOW_SYNTHETIC", "1")
    profile = station()
    client.post("/profiles", json=profile)
    started = client.post(
        "/live/start",
        json={
            "source": "synthetic",
            "record": True,
            "profile_id": profile["profile_id"],
            "source_kind": "physical_robot_station",
            "capture_attested_by": "owner",
        },
    )
    assert started.status_code == 200
    assert live.get_engine().source_kind == "simulation"


def test_physical_label_requires_named_operator_and_auth_before_open(camera_client, monkeypatch):
    client, main, live, sources = camera_client
    profile = station()
    client.post("/profiles", json=profile)
    assert (
        client.post(
            "/live/start",
            json={
                "source": "usb",
                "device_index": 2,
                "profile_id": profile["profile_id"],
                "record": True,
                "source_kind": "physical_robot_station",
            },
        ).status_code
        == 422
    )
    assert not sources
    monkeypatch.setenv("SIGNOFF_TOKEN", "test-camera-private-token")
    assert client.get("/live/cameras").status_code == 401
    assert (
        client.post("/live/preview/start", json={"source": "usb", "device_index": 2}).status_code
        == 401
    )
    assert not sources


@pytest.mark.parametrize(
    "body",
    [
        {"source": []},
        {"source": "usb", "device_index": True},
        {"source": "usb", "device_index": 2, "fps": 0},
        {"source": "usb", "device_index": 2, "width": float("inf")},
    ],
)
def test_preview_rejects_invalid_input_without_open(camera_client, body):
    client, main, live, sources = camera_client
    # JSON serialization forbids infinities; use a finite typed invalid width instead.
    if body.get("width") == float("inf"):
        body["width"] = "1280"
    assert client.post("/live/preview/start", json=body).status_code == 422
    assert not sources


def test_deadline_pacing_subtracts_blocking_read_and_processing_time():
    now = [0.0]
    pacer = DeadlinePacer(1 / 30, lambda: now[0])
    now[0] += 1 / 30 + 0.01  # hardware read consumed a full camera period plus processing
    assert pacer.remaining() == 0
    now[0] += 0.005  # a fast synthetic source should wait only for the remainder
    assert pacer.remaining() == pytest.approx(1 / 30 - 0.005)


def test_bounded_soak_aggregates_real_samples_conservatively():
    samples = SoakSamples()
    for _ in range(100000):
        samples.observation(3.2)
    samples.observation(7000)
    samples.event(4.5)
    samples.observation(-1)
    result = samples.snapshot()
    assert len(samples.histogram) == 2
    assert result["sample_count"] == 100001
    assert result["p95_ms"] == 4 and result["max_ms"] == 7000
    assert result["qualified_event_count"] == 1 and result["invalid_samples"] == 1


def test_preview_profile_transition_resets_recording_frame_clock_without_extra_read(tmp_path):
    source = FakeCamera(synthetic=True)
    engine = LiveEngine(source, EventBus(), mode="preview", data_dir=tmp_path)
    engine._process_image(marker_image(), 8000)
    assert engine.recorder is None and engine.session_id == ""
    engine.begin_recording(
        station(),
        tmp_path / "session",
        "session",
        source_kind="physical_robot_station",
        capture_attested_by="owner",
    )
    engine._process_image(marker_image(), 0)
    assert engine.pipeline.last.frame_no == 0 and engine.pipeline.last.ms == 0
    assert source.reads == 0 and engine.source_kind == "simulation"
    engine.stop()
    assert source.closed


def test_recording_preflight_disk_failure_retains_preview_without_archive(tmp_path, monkeypatch):
    source = FakeCamera()
    engine = LiveEngine(source, EventBus(), mode="preview", data_dir=tmp_path)
    engine._process_image(marker_image(), 0)
    import shutil

    original = shutil.disk_usage
    monkeypatch.setattr(shutil, "disk_usage", lambda path: original(path)._replace(free=0))
    with pytest.raises(RuntimeError, match="disk"):
        engine.begin_recording(station(), tmp_path / "session", "session")
    assert engine.mode == "preview" and engine.recorder is None
    assert not list((tmp_path / "session").glob("video/*.mkv"))
    monkeypatch.undo()
    engine.stop()


def test_soak_counts_archived_frames_only_after_recording_failure(tmp_path):
    source = FakeCamera(synthetic=True)
    engine = LiveEngine(
        source,
        EventBus(),
        profile=station(),
        session_dir=tmp_path,
        session_id="simulated-recording",
    )
    for n in range(3):
        engine._process_image(marker_image(), n * 100.0)
    from services.video_worker.recorder import RecordingUnavailable

    def failed_write(frame):
        engine.recorder.status = "interrupted"
        raise RecordingUnavailable("mock archive failure")

    engine.recorder.consume = failed_write
    engine._process_image(marker_image(), 300.0)
    engine._process_image(marker_image(), 400.0)
    engine.stop()
    receipt = json.loads((tmp_path / "live-soak.json").read_text())
    manifest = json.loads((tmp_path / "video" / "recording.manifest.json").read_text())
    assert receipt["observation_count"] == receipt["latency"]["sample_count"] == 3
    assert receipt["processed_observation_count"] == 5
    assert receipt["duration_ms"] == manifest["end_ms"]
    assert receipt["recording"]["integrity_verified"] is False
    assert "RECORDING_UNAVAILABLE" in receipt["quality_flags"]


def test_discovery_keeps_container_visible_nodes_and_stable_aliases(tmp_path):
    sys_root, dev_root = tmp_path / "sys", tmp_path / "dev"
    sys_root.mkdir()
    dev_root.mkdir()
    for number in (2, 9):
        node = sys_root / f"video{number}"
        node.mkdir()
        (node / "name").write_text(f"Camera {number}")
    (dev_root / "video2").touch()
    aliases = dev_root / "v4l" / "by-id"
    aliases.mkdir(parents=True)
    (aliases / "usb-stable-camera").symlink_to("../../video2")
    result = discover_cameras(sys_root, dev_root)
    assert [item["device_index"] for item in result] == [2]
    assert result[0]["stable_paths"] == [str(aliases / "usb-stable-camera")]
