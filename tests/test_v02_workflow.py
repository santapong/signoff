"""Operator workflow regressions for async evidence, revisions and auth."""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.analysis_worker.jobs import JobStore, atomic_json, sha256


def video_bytes(tmp_path: Path, suffix=".mp4") -> bytes:
    import cv2

    path = tmp_path / ("fixture" + suffix)
    codec = "mp4v" if suffix == ".mp4" else "FFV1"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), 10, (160, 120))
    for frame in range(30):
        image = np.zeros((120, 160, 3), dtype=np.uint8)
        image[20:40, 20 + frame : 40 + frame] = 255
        writer.write(image)
    writer.release()
    return path.read_bytes()


def fake_analyze(path, profile=None, clock_path=None):
    return {
        "detected_cycles": [
            {
                "cycle": 1,
                "cycle_id": "cycle-1",
                "status": "COMPLETE",
                "start_ms": 500.0,
                "end_ms": 1500.0,
            }
        ],
        "partial_cycles": [],
        "idle_intervals": [{"start_ms": 1500, "end_ms": 3000}],
        "video_duration_ms": 3000.0,
        "coverage": {"usable_ratio": 1.0, "observed_ms": 3000, "unknown_ms": 0},
        "quality_flags": [],
        "events": [],
        "provenance": {"timestamp_basis": "fixture"},
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNOFF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("SIGNOFF_TOKEN", raising=False)
    monkeypatch.delenv("SIGNOFF_ALLOW_SYNTHETIC", raising=False)
    from services.analysis_worker import jobs as job_module

    monkeypatch.setattr(job_module, "default_analyzer", fake_analyze)
    import apps.api.main as api_main

    importlib.reload(api_main)
    with TestClient(api_main.app) as client:
        yield api_main, client, tmp_path


def wait_run(client, url):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        item = client.get(url).json()
        if item["state"] in {"complete", "failed", "interrupted"}:
            assert item["state"] == "complete", item
            return item
        time.sleep(0.02)
    pytest.fail("analysis worker did not complete")


def upload_and_run(env, suffix=".mp4"):
    _, client, tmp_path = env
    response = client.post(
        "/sessions/upload",
        files={
            "video": ("input" + suffix, video_bytes(tmp_path, suffix), "application/octet-stream")
        },
        data={"notes": "form notes retained"},
    )
    assert response.status_code == 201, response.text
    session = response.json()
    result = client.post(f"/sessions/{session['session_id']}/analyze", json={})
    assert result.status_code == 202
    return session, wait_run(client, result.json()["status_url"])


@pytest.mark.parametrize("suffix", [".mp4", ".mkv"])
def test_upload_run_review_exports_are_frozen_and_evidence_resolves(env, suffix):
    main, client, _ = env
    session, run = upload_and_run(env, suffix)
    sid = session["session_id"]
    assert session["notes"] == "form notes retained"
    analysis = client.get(run["artifacts"]["analysis.json"]).json()
    assert analysis["metrics"]["outcomes"]["success_rate"] is None
    assert analysis["metrics"]["detection"]["available"] is False
    evidence_url = analysis["detected_cycles"][0]["evidence"]["url"]
    assert client.get(evidence_url).headers["content-type"].startswith("video/mp4")
    frozen_report = client.get(run["artifacts"]["report.json"]).json()
    assert frozen_report["detected_cycles"][0]["evidence_url"] == evidence_url
    assert evidence_url in client.get(run["artifacts"]["report.html"]).text
    old_report = client.get(run["artifacts"]["report.json"]).content
    row = {"cycle": 1, "start_ms": 500, "end_ms": 1500, "outcome": "OK"}
    first = client.post(f"/sessions/{sid}/review", json={"annotator": "owner", "cycles": [row]})
    assert first.status_code == 200
    row["outcome"] = "UNKNOWN"
    second = client.post(f"/sessions/{sid}/review", json={"annotator": "owner", "cycles": [row]})
    assert first.json()["revision_id"] != second.json()["revision_id"]
    assert len(list((main.DATA_DIR / "sessions" / sid / "reviews").glob("*.json"))) == 2
    assert client.get(run["artifacts"]["report.json"]).content == old_report
    new_run = client.post(f"/sessions/{sid}/analyze", json={}).json()
    assert new_run["run_id"] != run["run_id"]
    updated = wait_run(client, new_run["status_url"])
    assert updated["annotation_revision"] == second.json()["revision_id"]
    page = client.get(f"/ui/sessions/{sid}")
    assert page.status_code == 200 and "N/A" in page.text and "Save review revision" in page.text
    for name in ("report.json", "report.csv", "report.html", "report.md", "report.print.html"):
        assert client.get(updated["artifacts"][name]).status_code == 200
    acceptance = client.post(
        f"/ui/sessions/{sid}/acceptance",
        json={
            "run_id": updated["run_id"],
            "reviewer": "owner",
            "decision": "HOLD",
            "reason": "Waiting for real footage qualification",
        },
    )
    assert acceptance.status_code == 201 and acceptance.json()["pilot_type"] == "owner"


def test_tampered_report_and_source_are_blocked(env):
    main, client, _ = env
    session, run = upload_and_run(env)
    report_path = main.DATA_DIR / "runs" / run["run_id"] / "artifacts" / "report.json"
    original = report_path.read_bytes()
    report_path.write_text("{}")
    assert client.get(run["artifacts"]["report.json"]).status_code == 409
    report_path.write_bytes(original)
    source = main.DATA_DIR / "sessions" / session["session_id"] / "video" / session["video_file"]
    source.write_bytes(source.read_bytes() + b"tamper")
    assert client.get(run["artifacts"]["report.json"]).status_code == 409


def test_cookie_mutations_require_csrf_and_websocket_auth(env, monkeypatch):
    _, client, _ = env
    monkeypatch.setenv("SIGNOFF_TOKEN", "fixture-token")
    assert client.get("/sessions").status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/live/ws"):
            pass
    login = client.post("/ui/login", data={"token": "fixture-token"}, follow_redirects=False)
    assert login.status_code == 303
    assert "fixture-token" not in client.cookies.get("signoff_session")
    payload = {"profile_id": "bench", "detector": {"method": "motion"}}
    assert client.post("/profiles", json=payload).status_code == 403
    csrf = client.cookies.get("signoff_csrf")
    assert client.post("/profiles", json=payload, headers={"X-CSRF-Token": csrf}).status_code == 201
    assert (
        client.post(
            "/profiles",
            json={**payload, "profile_id": "bearer"},
            headers={"Authorization": "Bearer fixture-token"},
        ).status_code
        == 201
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/live/ws", headers={"Origin": "https://untrusted.example"}):
            pass


def test_explicit_live_inputs_do_not_access_device_when_invalid(env):
    _, client, _ = env
    assert client.post("/live/start").status_code == 422
    assert client.post("/profiles", json={"profile_id": "bench"}).status_code == 201
    assert (
        client.post("/live/start", json={"profile_id": "bench", "source": "usb"}).status_code == 422
    )
    assert (
        client.post("/live/start", json={"profile_id": "bench", "source": "synthetic"}).status_code
        == 403
    )
    assert client.post("/sessions/not.valid/analyze").status_code == 422


def test_restart_marks_running_and_capture_interrupted_preserving_complete(tmp_path):
    root = tmp_path / "data"
    for run_id, state in (("running-run", "running"), ("finished-run", "complete")):
        atomic_json(root / "runs" / run_id / "run.json", {"run_id": run_id, "state": state})
    atomic_json(
        root / "sessions" / "captured" / "session.json",
        {"recording_state": "running", "session_id": "captured"},
    )
    store = JobStore(root)
    try:
        assert store.get("running-run")["state"] == "interrupted"
        assert store.get("finished-run")["state"] == "complete"
        session = json.loads((root / "sessions" / "captured" / "session.json").read_text())
        assert session["recording_state"] == "interrupted"
        with pytest.raises(RuntimeError, match="another application"):
            JobStore(root)
    finally:
        store.stop()


def test_no_complete_publication_if_analysis_fails(tmp_path):
    root = tmp_path / "data"
    video = root / "sessions" / "one" / "video" / "source.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"fixture")

    def fail(*args):
        raise ValueError("invalid clock")

    store = JobStore(root, analyzer=fail)
    item = store.enqueue(
        {"session_id": "one", "video_file": "source.mp4", "video_sha256": sha256(video)}
    )
    try:
        deadline = time.monotonic() + 5
        while store.get(item["run_id"])["state"] not in {"failed", "complete"}:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert store.get(item["run_id"])["state"] == "failed"
        assert "invalid clock" in store.get(item["run_id"])["reason"]
        assert not (root / "runs" / item["run_id"] / "artifacts").exists()
    finally:
        store.stop()


def test_explicit_synthetic_live_archives_shared_observations_and_releases_owner(env, monkeypatch):
    main, client, _ = env
    monkeypatch.setenv("SIGNOFF_ALLOW_SYNTHETIC", "1")
    response = client.post(
        "/profiles",
        json={
            "profile_id": "live-bench",
            "camera": {"width": 160, "height": 120, "fps": 10},
            "detector": {"method": "motion"},
        },
    )
    assert response.status_code == 201
    start = client.post(
        "/live/start", json={"profile_id": "live-bench", "source": "synthetic", "record": True}
    )
    assert start.status_code == 200, start.text
    sid = start.json()["session_id"]
    try:
        assert (
            client.post(
                "/live/start",
                json={"profile_id": "live-bench", "source": "synthetic", "record": True},
            ).status_code
            == 409
        )
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            status = client.get("/live/status").json()
            if status["completed_cycles"]:
                break
            time.sleep(0.1)
        assert status["completed_cycles"] >= 1
        assert status["source"] == "synthetic_development"
        assert status["recording_state"] == "recording"
        assert client.get("/live/preview").status_code == 200
        assert client.post(f"/sessions/{sid}/analyze").status_code == 409
    finally:
        assert client.post("/live/stop").status_code == 200
    assert client.get("/live/status").json()["running"] is False
    session = main.session_info(sid)
    assert session["recording_state"] == "stopped"
    manifest = main.DATA_DIR / "sessions" / sid / "video" / session["video_file"]
    assert json.loads(manifest.read_text())["segments"]
    run = client.post(f"/sessions/{sid}/analyze").json()
    assert wait_run(client, run["status_url"])["state"] == "complete"
    bound = client.post(
        f"/sessions/{sid}/experiment",
        json={
            "experiment_id": "EXP-OWNER",
            "controller_label": "scripted",
            "policy_label": "baseline",
            "protocol_id": "camera-v2-unqualified",
        },
    )
    assert bound.status_code == 201
    assert main.session_info(sid)["controller_label"] == "scripted"
