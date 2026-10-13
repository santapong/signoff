"""Durable operator job recovery and review revisions preserve frozen evidence."""
from __future__ import annotations

import json

from services.analysis_worker.jobs import JobStore, atomic_json
from tests import test_v02_workflow as workflow

env = workflow.env
upload_and_run = workflow.upload_and_run


def test_duplicate_active_analysis_is_reused_but_new_review_gets_new_run(tmp_path, monkeypatch):
    store = JobStore(tmp_path)
    monkeypatch.setattr(store, "start", lambda: None)
    try:
        session = {"session_id": "ses-one", "video_sha256": "a" * 64}
        first = store.enqueue(session)
        assert store.enqueue(session)["run_id"] == first["run_id"]
        assert store.pending.qsize() == 1
        atomic_json(tmp_path / "sessions/ses-one/reviews/rev-one.json",
                    {"revision_id": "rev-one", "cycles": []})
        second = store.enqueue(session)
        assert second["run_id"] != first["run_id"]
        assert second["annotation_revision"] == "rev-one"
        assert store.get(first["run_id"])["annotation_revision"] is None
        other = store.enqueue({"session_id": "ses-two", "video_sha256": "b" * 64})
        assert other["run_id"] not in {row["run_id"] for row in store.session_runs("ses-one")}
    finally:
        store.stop()


def test_reloaded_page_can_recover_active_job_without_source_payload(env, monkeypatch):
    main, client, _ = env
    session, complete = upload_and_run(env)
    store = main.jobs()
    store.stop()
    monkeypatch.setattr(store, "start", lambda: None)
    active = client.post(f"/sessions/{session['session_id']}/analyze", json={}).json()
    result = client.get(f"/sessions/{session['session_id']}/runs").json()
    assert result["active_run"]["run_id"] == active["run_id"]
    assert result["latest_complete_run"] == complete["run_id"]
    assert all("session" not in row and "reviewed_cycles" not in row for row in result["runs"])
    assert client.get('/sessions/unknown/runs').status_code == 404


def test_review_notes_are_revisioned_and_cannot_modify_frozen_report(env):
    main, client, _ = env
    session, run = upload_and_run(env)
    original = client.get(run["artifacts"]["report.json"]).content
    body = {"run_id": run["run_id"], "annotator": "owner", "cycles": [
        {"cycle": 1, "start_ms": 500, "end_ms": 1500, "outcome": "UNKNOWN",
         "notes": "Object transfer is unclear."}]}
    response = client.post(f"/sessions/{session['session_id']}/review", json=body)
    assert response.status_code == 200
    revision = json.loads((main.DATA_DIR / "sessions" / session["session_id"] / "reviews"
                           / (response.json()["revision_id"] + '.json')).read_text())
    assert revision["cycles"][0]["notes"] == "Object transfer is unclear."
    assert client.get(run["artifacts"]["report.json"]).content == original
    body["cycles"][0]["notes"] = "x" * 2001
    assert client.post(f"/sessions/{session['session_id']}/review", json=body).status_code == 422


def test_source_attestation_never_certifies_physical_accuracy(env):
    import pytest
    from fastapi import HTTPException

    from apps.api.main import capture_metadata

    with pytest.raises(HTTPException) as exc:
        capture_metadata("physical_robot_station")
    assert exc.value.status_code == 422
    metadata = capture_metadata("physical_robot_station", "Owner", "Station first run")
    assert metadata["capture_attested_by"] == "Owner"
    assert metadata["physical_capture_verified"] is False
    assert capture_metadata("simulation")["source_kind"] == "simulation"
