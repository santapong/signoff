"""Blinded original-frame and immutable reference API regressions."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_v02_reference import vfr_media

from packages.domain.profiles import validate_profile
from packages.domain.qualification_workspace import QualificationWorkspace
from packages.domain.reference_workspace import ReferenceWorkspace
from tools.qualify_dataset import _sha


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "data"
    session = root / "sessions" / "ses-original"
    video_dir = session / "video"
    video_dir.mkdir(parents=True)
    media = vfr_media(video_dir)
    profile = validate_profile({"profile_id": "station"})
    (session / "session.json").write_text(
        json.dumps(
            {
                "session_id": "ses-original",
                "video_file": media.name,
                "video_sha256": _sha(media),
                "profile": profile,
                "source_kind": "simulation",
            }
        )
    )
    # Poison prediction artifacts; the reference workspace must never read them.
    (session / "analysis.json").write_text("poison prediction content")
    (session / "reviews").mkdir()
    (session / "reviews" / "predictions.json").write_text("poison review content")
    monkeypatch.setenv("SIGNOFF_DATA_DIR", str(root))
    monkeypatch.delenv("SIGNOFF_TOKEN", raising=False)
    import apps.api.evidence as api
    import apps.api.main as main

    importlib.reload(main)
    if not any(getattr(route, "path", "").startswith("/evidence/") for route in main.app.routes):
        main.app.include_router(api.router)
    return root, session, media, TestClient(main.app)


def _body(index, reviewer="owner", **extra):
    return {
        "reviewer": reviewer,
        "reviewer_kind": "human" if reviewer == "owner" else "ai_assisted",
        "kind": "cycles",
        "independently_labeled": True,
        "source_hash": index["source_hash"],
        "index_hash": index["index_hash"],
        "rows": [{"cycle": 1, "start_frame": 1, "end_frame": 3, "outcome": "UNKNOWN"}],
        **extra,
    }


def test_source_index_frame_and_reference_path_are_blinded_and_use_vfr_clock(source, monkeypatch):
    _, session, _, client = source
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        if path.name == "analysis.json" or "reviews" in path.parts:
            pytest.fail("blinded editor read predictions")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    response = client.get("/evidence/sessions/ses-original/source/index?limit=2")
    assert response.status_code == 200, response.text
    index = response.json()
    assert index["blinded"] is True and index["total_frames"] == 4
    assert [frame["ms"] for frame in index["frames"]] == [0, 80]
    assert "detected" not in response.text and str(session) not in response.text
    image = client.get(
        "/evidence/sessions/ses-original/source/frames/1",
        params={"source_hash": index["source_hash"]},
    )
    assert image.status_code == 200 and image.content.startswith(b"\xff\xd8")
    result = client.post("/evidence/sessions/ses-original/references", json=_body(index))
    assert result.status_code == 201, result.text
    revision = result.json()
    assert revision["rows"][0]["start_ms"] == 80
    assert revision["rows"][0]["end_ms"] == 500
    exported = client.get(
        f"/evidence/sessions/ses-original/references/{revision['revision_id']}/export"
    )
    assert exported.status_code == 200
    assert json.loads(exported.text)["annotation_role"] == "REFERENCE"
    assert "detected" not in exported.text


def test_reference_revisions_are_immutable_and_lineage_is_distinct(source):
    _, _, _, client = source
    index = client.get("/evidence/sessions/ses-original/source/index").json()
    first = client.post("/evidence/sessions/ses-original/references", json=_body(index)).json()
    second = client.post(
        "/evidence/sessions/ses-original/references", json=_body(index, "assistant")
    ).json()
    changed = client.post(
        "/evidence/sessions/ses-original/references",
        json=_body(
            index,
            supersedes=first["revision_id"],
            rows=[{"cycle": 1, "start_frame": 0, "end_frame": 3}],
        ),
    ).json()
    assert first["revision_id"] != changed["revision_id"]
    loaded = client.get("/evidence/sessions/ses-original/references/" + first["revision_id"]).json()
    assert loaded["rows"][0]["start_ms"] == 80
    response = client.post(
        "/evidence/sessions/ses-original/adjudications",
        json={
            "reviewer": "owner",
            "reason": "Review source boundaries",
            "reference_revision": changed["revision_id"],
            "annotation_revisions": [first["revision_id"], second["revision_id"]],
        },
    )
    assert response.status_code == 201, response.text
    summaries = client.get("/evidence/sessions/ses-original/references").json()["revisions"]
    assert {row["reviewer_kind"] for row in summaries} == {"human", "ai_assisted"}
    assert all(row["row_count"] == 1 for row in summaries)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_hash": "stale"},
        {"index_hash": "stale"},
        {"rows": [{"cycle": 1, "start_frame": 100, "end_frame": 3}]},
        {"rows": [{"cycle": 1, "start_frame": 3, "end_frame": 1}]},
        {"independently_labeled": False},
        {"reviewer_kind": "unknown"},
    ],
)
def test_invalid_or_stale_source_boundary_selection_rejected(source, changes):
    _, _, _, client = source
    index = client.get("/evidence/sessions/ses-original/source/index").json()
    response = client.post(
        "/evidence/sessions/ses-original/references", json=_body(index, **changes)
    )
    assert response.status_code == 422


def test_added_or_tampered_sidecar_cannot_reuse_old_source_index(source):
    _, session, media, _ = source
    workspace = ReferenceWorkspace(session)
    workspace.index()
    media.with_suffix(".clock.jsonl").write_text('{"frame":0,"ms":0}\n')
    with pytest.raises(ValueError, match="clock selection changed"):
        workspace.index()


def test_dataset_gui_api_freezes_original_refs_and_pending_receipt(source):
    root, session, _, client = source
    index = client.get("/evidence/sessions/ses-original/source/index").json()
    first = client.post("/evidence/sessions/ses-original/references", json=_body(index)).json()
    second = client.post(
        "/evidence/sessions/ses-original/references", json=_body(index, "assistant")
    ).json()
    lineage = client.post(
        "/evidence/sessions/ses-original/adjudications",
        json={
            "reviewer": "owner",
            "reason": "Agree on original frame boundaries",
            "reference_revision": first["revision_id"],
            "annotation_revisions": [first["revision_id"], second["revision_id"]],
        },
    ).json()
    response = client.post(
        "/evidence/qualification/datasets",
        json={
            "dataset_id": "screening",
            "name": "Screening rehearsal",
            "source_kind": "simulation",
        },
    )
    assert response.status_code == 201, response.text
    assignments = {
        "sessions": [
            {
                "session_id": "ses-original",
                "split": "test",
                "reference_revision": first["revision_id"],
                "annotation_revisions": [first["revision_id"], second["revision_id"]],
                "adjudication_revision": lineage["adjudication_id"],
            }
        ]
    }
    assert (
        client.put(
            "/evidence/qualification/datasets/screening/assignments", json=assignments
        ).status_code
        == 200
    )
    frozen = client.post(
        "/evidence/qualification/datasets/screening/freeze", json={"attested_by": "owner"}
    )
    assert frozen.status_code == 201, frozen.text
    freeze_id = frozen.json()["freeze_id"]
    directory = root / "qualification" / "datasets" / "screening" / "freezes" / freeze_id
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["source_kind"] == "simulation"
    assert manifest["sessions"][0]["annotations"][1]["annotator"] == "assistant"
    assert (directory / manifest["sessions"][0]["video"]).read_bytes() == (
        session / "video" / "source.mkv"
    ).read_bytes()
    job = client.post("/evidence/qualification/datasets/screening/evaluate").json()
    store = next(
        value
        for value in __import__("apps.api.evidence", fromlist=["_workspaces"])._workspaces.values()
        if value.data == root.resolve()
    )
    store.executor.shutdown(wait=True)
    result = client.get("/evidence/qualification/jobs/" + job["job_id"]).json()
    assert result["status"] == "complete", result
    receipt = client.get("/evidence/qualification/receipts/" + job["receipt_id"]).json()
    assert receipt["receipt"]["qualification"]["status"] == "pending"
    assert client.get(receipt["download_url"]).status_code == 200


def test_dataset_import_cannot_read_arbitrary_files(source):
    root, _, _, _ = source
    store = QualificationWorkspace(root, root / "approved")
    with pytest.raises(ValueError, match="identifier"):
        store.import_candidate("../../etc")
    assert store.list()["approved_candidates"] == []
