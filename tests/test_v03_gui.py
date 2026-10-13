"""Workbench routes preserve source/review identity and reference isolation."""

from __future__ import annotations

import importlib
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from tools.generate_demo import generate_demo


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNOFF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("SIGNOFF_TOKEN", raising=False)
    monkeypatch.delenv("SIGNOFF_ALLOW_SYNTHETIC", raising=False)
    import apps.api.main as main

    importlib.reload(main)
    # Actual additive evidence router; root mounts it at final integration.
    try:
        from apps.api.evidence import router

        if "/evidence/qualification/datasets" not in main.app.openapi()["paths"]:
            main.app.include_router(router)
    except ImportError:
        pass
    with TestClient(main.app) as tc:
        yield tc, main, tmp_path


def upload(client):
    tc, main, temporary = client
    directory = temporary / "generated"
    generate_demo(directory, cycles=1)
    profile = json.loads((directory / "profile.json").read_text())
    stored = tc.post("/profiles", json=profile)
    assert stored.status_code == 201, stored.text
    response = tc.post(
        "/ui/upload",
        files={"video": ("original.mp4", (directory / "demo.mp4").read_bytes(), "video/mp4")},
        data={
            "profile_id": stored.json()["profile_id"],
            "source_kind": "simulation",
            "session_name": "Operator fixture",
            "notes": "Generated evidence; no physical claim",
        },
    )
    assert response.status_code == 200, response.text
    sid = response.json()["redirect"].rsplit("/", 1)[-1]
    assert main.session_info(sid)["source_kind"] == "simulation"
    return sid


def analyze(client, sid):
    tc, _, _ = client
    response = tc.post(f"/sessions/{sid}/analyze")
    assert response.status_code == 202
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run = tc.get(response.json()["status_url"]).json()
        if run["state"] in ("complete", "failed", "interrupted"):
            assert run["state"] == "complete", run
            return run
        time.sleep(0.03)
    pytest.fail("actual analysis run did not finish")


def page_data(html, identity):
    match = re.search(
        r'<script id="' + identity + r'" type="application/json">(.*?)</script>', html, re.S
    )
    assert match
    return json.loads(match.group(1))


def test_navigation_and_setup_render_without_opening_camera(client, monkeypatch):
    tc, _, _ = client
    import apps.api.live as live

    def forbidden(*args, **kwargs):
        raise AssertionError("navigation must not open a camera")

    monkeypatch.setattr(live, "make_source", forbidden)
    for route in ("/ui/", "/ui/profiles", "/live/", "/ui/upload", "/ui/qualification"):
        response = tc.get(route)
        assert response.status_code == 200, response.text
        assert "Stations" in response.text and "Qualification" in response.text
    setup = tc.get("/ui/profiles").text
    assert 'name="name"' in setup and 'id="region-editor"' in setup
    assert "Profile JSON" not in setup
    assert "No saved stations" in setup


def test_source_identity_and_frozen_review_workflow_render_actual_data(client):
    tc, main, _ = client
    sid = upload(client)
    run = analyze(client, sid)
    source_report = tc.get(run["artifacts"]["report.json"]).content
    response = tc.get(f"/ui/sessions/{sid}")
    assert response.status_code == 200, response.text
    context = page_data(response.text, "workbench-data")
    assert context["run_id"] == run["run_id"]
    assert len(context["cycles"]) == 1
    cycle = context["cycles"][0]
    assert cycle["timeline_left"] >= 0 and cycle["timeline_width"] > 0
    assert "Simulation" in response.text and "Physical qualification pending" in response.text
    assert tc.get(cycle["evidence"]["url"]).status_code == 200
    review = tc.post(
        f"/sessions/{sid}/review",
        json={
            "run_id": run["run_id"],
            "annotator": "owner",
            "cycles": [
                {
                    "cycle": cycle["cycle"],
                    "start_ms": cycle["start_ms"],
                    "end_ms": cycle["end_ms"],
                    "outcome": "UNKNOWN",
                    "notes": "Operator inspected original evidence",
                }
            ],
        },
    )
    assert review.status_code == 200, review.text
    changed = tc.get(f"/ui/sessions/{sid}")
    assert changed.status_code == 200 and 'id="stale-report"' in changed.text
    assert tc.get(run["artifacts"]["report.json"]).content == source_report
    assert main.session_info(sid)["name"] == "Operator fixture"


def test_reference_page_does_not_read_or_embed_predictions(client, monkeypatch):
    tc, main, _ = client
    sid = upload(client)

    def forbidden(*args, **kwargs):
        raise AssertionError("reference page cannot consult predictions")

    monkeypatch.setattr(main, "_analysis_cache", forbidden)
    monkeypatch.setattr(main, "review_list", forbidden)
    page = tc.get(f"/ui/references/{sid}")
    assert page.status_code == 200, page.text
    assert page_data(page.text, "reference-data") == {"session_id": sid}
    assert "Original frames" in page.text
    assert 'name="independently_labeled" required' in page.text
    assert '"detected_cycles"' not in page.text and '"workbench-data"' not in page.text


def test_owner_decision_returns_downloadable_exact_receipt(client):
    tc, _, _ = client
    sid = upload(client)
    run = analyze(client, sid)
    result = tc.post(
        f"/ui/sessions/{sid}/acceptance",
        json={
            "run_id": run["run_id"],
            "reviewer": "owner",
            "decision": "HOLD",
            "reason": "Physical source is not available",
        },
    )
    assert result.status_code == 201, result.text
    receipt = tc.get(result.json()["download_url"])
    assert receipt.status_code == 200
    assert receipt.json()["run_id"] == run["run_id"]
    assert receipt.json()["report_hash"] == result.json()["report_hash"]
    assert receipt.json()["reviewer_kind"] == "OWNER"


def test_names_are_escaped_and_gui_mutations_keep_csrf_protection(client, monkeypatch):
    tc, _, _ = client
    sid = upload(client)
    monkeypatch.setenv("SIGNOFF_TOKEN", "ui-test-only")
    assert tc.get(f"/ui/sessions/{sid}", follow_redirects=False).status_code == 303
    login = tc.post("/ui/login", data={"token": "ui-test-only"}, follow_redirects=False)
    assert login.status_code == 303
    assert tc.post("/profiles", json={"profile_id": "blocked"}).status_code == 403
    csrf = tc.cookies.get("signoff_csrf")
    created = tc.post(
        "/profiles",
        json={"profile_id": "escaped", "name": '<script>alert("x")</script>'},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    html = tc.get("/ui/profiles").text
    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;" in html


def test_reference_source_routes_and_dataset_draft_work_without_predictions(client, monkeypatch):
    tc, main, _ = client
    sid = upload(client)

    def forbidden(*args, **kwargs):
        raise AssertionError("independent references cannot use analysis")

    monkeypatch.setattr(main, "_analysis_cache", forbidden)
    index = tc.get(f"/evidence/sessions/{sid}/source/index?offset=0&limit=200")
    assert index.status_code == 200, index.text
    clock = index.json()
    assert clock["blinded"] is True and clock["frames"]
    frame = clock["frames"][0]
    pixels = tc.get(
        f"/evidence/sessions/{sid}/source/frames/{frame['frame']}",
        params={"source_hash": clock["source_hash"]},
    )
    assert pixels.status_code == 200 and pixels.headers["content-type"] == "image/jpeg"
    reference = tc.post(
        f"/evidence/sessions/{sid}/references",
        json={
            "reviewer": "alice",
            "reviewer_kind": "human",
            "independently_labeled": True,
            "kind": "cycles",
            "source_hash": clock["source_hash"],
            "index_hash": clock["index_hash"],
            "rows": [
                {
                    "cycle": 1,
                    "start_frame": clock["frames"][1]["frame"],
                    "end_frame": clock["frames"][-2]["frame"],
                    "outcome": "UNKNOWN",
                    "failure_type": "NONE",
                }
            ],
        },
    )
    assert reference.status_code == 201, reference.text
    saved = tc.get(f"/evidence/sessions/{sid}/references").json()
    assert saved["revisions"][0]["revision_id"] == reference.json()["revision_id"]
    dataset = tc.post(
        "/evidence/qualification/datasets",
        json={
            "dataset_id": "gui-test",
            "name": "GUI simulation draft",
            "source_kind": "simulation",
            "protocol_id": "camera-pick-place-v1",
            "capture_attested_by": "",
        },
    )
    assert dataset.status_code == 201, dataset.text
    assert dataset.json()["status"] == "draft"
    listing = tc.get("/evidence/qualification/datasets").json()
    assert listing["datasets"][0]["dataset_id"] == "gui-test"


def test_selected_qualification_binding_keeps_verified_hashes_and_exact_ack(client, monkeypatch):
    tc, _, _ = client
    sid = upload(client)
    run = analyze(client, sid)
    import apps.api.evidence as evidence

    def verified_binding(*args):
        return {
            "qualification_id": "receipt-test",
            "qualification_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "inactivity_review_hash": "c" * 64,
            "physical_ready": False,
        }

    monkeypatch.setattr(evidence, "qualification_bindings", verified_binding)
    payload = {
        "run_id": run["run_id"],
        "reviewer": "owner",
        "decision": "HOLD",
        "reason": "Waiting for physical qualification",
        "qualification_id": "receipt-test",
    }
    assert tc.post(f"/ui/sessions/{sid}/acceptance", json=payload).status_code == 422
    payload["inactivity_acknowledged"] = True
    response = tc.post(f"/ui/sessions/{sid}/acceptance", json=payload)
    assert response.status_code == 201, response.text
    saved = tc.get(response.json()["download_url"]).json()
    assert saved["inactivity_acknowledged"] is True
    assert saved["inactivity_review_hash"] == "c" * 64
    assert saved["qualification_sha256"] == "a" * 64
    payload["manifest_sha256"] = "d" * 64
    assert tc.post(f"/ui/sessions/{sid}/acceptance", json=payload).status_code == 422
