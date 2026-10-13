"""Additive reference/qualification APIs; blinded routes read original source only."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response

from packages.domain.qualification_workspace import QualificationWorkspace
from packages.domain.reference_workspace import ReferenceWorkspace, digest
from packages.reporting.signoff_report import verify_report
from tools.qualify_dataset import _sha, qualification_receipt_hash, validate_frozen_inputs

router = APIRouter(prefix="/evidence", tags=["source references and qualification"])
_workspaces: dict[str, QualificationWorkspace] = {}
_WORKSPACE_LOCK = threading.RLock()


def workspace() -> QualificationWorkspace:
    from apps.api.main import DATA_DIR

    approved = os.environ.get("SIGNOFF_QUALIFICATION_ROOT")
    key = str(DATA_DIR.resolve()) + ":" + str(approved)
    with _WORKSPACE_LOCK:
        if key not in _workspaces:
            _workspaces[key] = QualificationWorkspace(
                DATA_DIR, Path(approved) if approved else None
            )
        return _workspaces[key]


def references(sid: str) -> ReferenceWorkspace:
    from apps.api.main import session_path

    return ReferenceWorkspace(session_path(sid))


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except FileNotFoundError as exc:
        raise HTTPException(
            404, "selected source/reference/dataset evidence does not exist"
        ) from exc
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/sessions/{sid}/source/index")
def source_index(sid: str, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)):
    index = _call(lambda: references(sid).index())
    frames = [
        {
            key: row.get(key)
            for key in (
                "frame",
                "ms",
                "segment_frame",
                "segment_id",
                "clock_basis",
                "quality_flags",
            )
        }
        for row in index["frames"][offset : offset + limit]
    ]
    return {
        key: index[key]
        for key in ("session_id", "source_hash", "index_hash", "video_duration_ms", "blinded")
    } | {
        "frames": frames,
        "total_frames": len(index["frames"]),
        "offset": offset,
        "next_offset": offset + len(frames)
        if offset + len(frames) < len(index["frames"])
        else None,
    }


@router.get("/sessions/{sid}/source/frames/{frame}")
def source_frame(sid: str, frame: int, source_hash: str):
    encoded = _call(lambda: references(sid).frame_jpeg(frame, source_hash))
    return Response(encoded, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/sessions/{sid}/references")
def reference_list(sid: str):
    return {"session_id": sid, "blinded": True, "revisions": _call(lambda: references(sid).list())}


@router.post("/sessions/{sid}/references", status_code=201)
def reference_create(sid: str, payload: dict = Body(...)):
    return _call(lambda: references(sid).create(payload))


@router.get("/sessions/{sid}/references/{revision_id}/export", response_class=PlainTextResponse)
def reference_export(sid: str, revision_id: str):
    revision = _call(lambda: references(sid).get(revision_id))
    return PlainTextResponse(
        ReferenceWorkspace.export_rows(revision["rows"]),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{revision_id}.jsonl"'},
    )


@router.get("/sessions/{sid}/references/{revision_id}")
def reference_get(sid: str, revision_id: str):
    return _call(lambda: references(sid).get(revision_id))


@router.get("/sessions/{sid}/adjudications")
def adjudication_list(sid: str):
    return {"adjudications": _call(lambda: references(sid).adjudications())}


@router.post("/sessions/{sid}/adjudications", status_code=201)
def adjudication_create(sid: str, payload: dict = Body(...)):
    return _call(lambda: references(sid).adjudicate(payload))


@router.get("/qualification/datasets")
def dataset_list():
    return _call(lambda: workspace().list())


@router.post("/qualification/datasets", status_code=201)
def dataset_create(payload: dict = Body(...)):
    return _call(lambda: workspace().create(payload))


@router.post("/qualification/import", status_code=201)
def dataset_import(payload: dict = Body(...)):
    return _call(lambda: workspace().import_candidate(payload.get("candidate_id", "")))


@router.get("/qualification/datasets/{dataset_id}")
def dataset_get(dataset_id: str):
    return _call(lambda: workspace().get(dataset_id))


@router.put("/qualification/datasets/{dataset_id}/assignments")
def dataset_assign(dataset_id: str, payload: dict = Body(...)):
    return _call(lambda: workspace().assign(dataset_id, payload))


@router.post("/qualification/datasets/{dataset_id}/freeze", status_code=201)
def dataset_freeze(dataset_id: str, payload: dict = Body(...)):
    return _call(lambda: workspace().freeze(dataset_id, payload))


@router.post("/qualification/datasets/{dataset_id}/evaluate", status_code=202)
def dataset_evaluate(dataset_id: str):
    return _call(lambda: workspace().submit(dataset_id))


@router.get("/qualification/jobs/{job_id}")
def qualification_job(job_id: str):
    return _call(lambda: workspace().job(job_id))


@router.get("/qualification/receipts")
def qualification_receipts():
    return {"receipts": _call(lambda: workspace().receipts())}


@router.get("/qualification/receipts/{qualification_id}")
def qualification_receipt(qualification_id: str):
    return _call(lambda: workspace().receipt(qualification_id))


@router.get("/qualification/receipts/{qualification_id}/export")
def qualification_export(qualification_id: str):
    item = _call(lambda: workspace().receipt(qualification_id))
    return Response(
        json.dumps(item["receipt"], indent=2, sort_keys=True),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{qualification_id}.json"'},
    )


def qualification_bindings(data_root: Path, qualification_id: str, sid: str, run_id: str) -> dict:
    """Verify selected immutable receipt/manifest/report, without changing them."""
    from apps.api.main import identifier, jobs, session_path

    sid = identifier(sid)
    run_id = identifier(run_id)
    session_path(sid)

    store = workspace()
    if store.data != Path(data_root).resolve():
        raise ValueError("qualification workspace does not match application data root")
    item = store.receipt(qualification_id)
    job = store.job(qualification_id)
    receipt = item["receipt"]
    frozen = store.directory(job["dataset_id"]) / "freezes" / job["freeze_id"]
    if _sha(frozen / "manifest.json") != item["manifest_sha256"]:
        raise ValueError("selected qualification manifest changed")
    validate_frozen_inputs(frozen / "manifest.json", frozen)
    if receipt.get("receipt_hash") != qualification_receipt_hash(receipt):
        raise ValueError("selected qualification receipt hash changed")
    report_path = jobs().artifact(run_id, "report.json")
    report = json.loads(report_path.read_text())
    if not verify_report(report) or report["session"]["session_id"] != sid:
        raise ValueError("selected report does not belong to this session")
    provenance, scope = report["provenance"], receipt["scope"]
    if any(
        provenance.get(key) != scope.get(key)
        for key in ("profile_hash", "configuration_hash", "code_sha")
    ):
        raise ValueError("selected report differs from the receipt's measurement scope")
    source = provenance.get("source_sha256", provenance.get("video_sha256"))
    if not any(
        row["session_id"] == sid and row["input_hashes"]["video_sha256"] == source
        for row in receipt["sessions"]
    ):
        raise ValueError("selected report source is absent from qualification evidence")
    inactivity = receipt.get("inactivity", {})
    return {
        "qualification_id": qualification_id,
        "qualification_sha256": item["sha256"],
        "manifest_sha256": item["manifest_sha256"],
        "report_sha256": _sha(report_path),
        "report_hash": report["report_hash"],
        "physical_ready": receipt["qualification"].get("physically_qualified", False),
        "inactivity_review_hash": inactivity.get("review_hash", digest(inactivity)),
        "inactivity_review": inactivity,
    }


def shutdown_workspaces(data_root: Path, *, wait: bool = True) -> int:
    """Close only previously created workers; importing/stopping opens no workspace."""
    root = Path(data_root).resolve()
    with _WORKSPACE_LOCK:
        selected = [(key, value) for key, value in _workspaces.items() if value.data == root]
        for key, _value in selected:
            _workspaces.pop(key)
    for _key, value in selected:
        value.close(wait=wait)
    return len(selected)


@router.post("/qualification/owner-binding", status_code=201)
def qualification_owner_binding(payload: dict = Body(...)):
    from apps.api.main import DATA_DIR
    from apps.api.ui import owner_acceptance

    sid, run_id = payload.get("session_id", ""), payload.get("run_id", "")
    binding = _call(
        qualification_bindings, DATA_DIR, payload.get("qualification_id", ""), sid, run_id
    )
    if payload.get("inactivity_acknowledged") is not True:
        raise HTTPException(
            422, "review displayed inactivity evidence before binding owner acceptance"
        )
    return owner_acceptance(sid, {**payload, **binding, "inactivity_acknowledged": True})
