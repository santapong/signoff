"""Small server-rendered operator workspace with working vanilla controls."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from services.analysis_worker.jobs import atomic_json, utcnow

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def clock_time(ms):
    if ms is None:
        return "N/A"
    seconds = max(0.0, float(ms)) / 1000
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


templates.env.filters["clock_time"] = clock_time


def source_labels(item: dict) -> dict:
    acquisition = {
        "recorded_upload": "Uploaded recording",
        "usb_camera": "USB webcam",
        "synthetic_development": "Synthetic development input",
    }
    kinds = {
        "physical_robot_station": "Physical source · owner attested",
        "manual_tabletop_rehearsal": "Tabletop rehearsal",
        "simulation": "Simulation",
        "public_robot_video": "Public robot footage",
        "unknown": "Source not declared",
    }
    return {
        "source_label": acquisition.get(item.get("source"), item.get("source", "Legacy source")),
        "source_kind_label": kinds.get(item.get("source_kind", "unknown"), "Source not declared"),
        "title": item.get("name")
        or item.get("session_name")
        or (item.get("notes", "").split("\n")[0][:70])
        or item.get("session_id", "Session"),
    }


NAV = """<nav class="nav"><a href="/ui/">Sessions</a><a href="/ui/profiles">Station setup</a>
<a href="/ui/upload">Upload</a><a href="/live/">Live monitor</a><a href="/docs">API</a></nav>"""
LOGIN_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signoff — Pilot sign-in</title><link rel="stylesheet" href="/static/workbench.css">
</head><body class="login-layout"><main class="login-card"><div class="brand">SIGNOFF</div>
<h1>Pilot sign-in</h1><p class="small muted">Open your local camera evidence workspace.</p>
<form method="post" action="/ui/login"><label class="field">Access token
<input type="password" name="token" required autocomplete="current-password"></label>
<button class="primary" type="submit">Sign in</button></form></main></body></html>"""


@router.get("/login", response_class=HTMLResponse)
def login_form():
    return HTMLResponse(LOGIN_HTML)


@router.post("/login")
def login_submit(request: Request, token: str = Form(...)):
    from apps.api.auth import expected_token, session_cookie

    if expected_token() and not hmac.compare_digest(token, expected_token()):
        return HTMLResponse("<p>Invalid token.</p>" + LOGIN_HTML, status_code=401)
    response = RedirectResponse("/ui/", status_code=303)
    response.set_cookie(
        "signoff_session",
        session_cookie(),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=8 * 3600,
    )
    response.set_cookie(
        "signoff_csrf",
        secrets.token_urlsafe(24),
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=8 * 3600,
    )
    return response


def _data_dir() -> Path:
    from apps.api.main import DATA_DIR

    return DATA_DIR


def _session_state(sid: str) -> dict:
    from apps.api.main import _analysis_cache, latest_run, session_info

    info = {
        **session_info(sid),
        "id": sid,
        "analyzed": bool(latest_run(sid)),
        "f1": None,
        "cycles": None,
        "success_rate": None,
    }
    if info["analyzed"]:
        try:
            metrics = _analysis_cache(sid)["metrics"]
            info.update(
                cycles=metrics.get("cycle_durations", {}).get("n"),
                success_rate=metrics.get("outcomes", {}).get("success_rate"),
                f1=metrics.get("detection", {}).get("f1"),
            )
        except HTTPException:
            info["integrity_warning"] = "Evidence integrity requires attention"
    info.update(source_labels(info))
    return info


def render(request: Request, name: str, **context):
    section = (
        "stations"
        if name == "profiles.html"
        else "capture"
        if name == "live.html"
        else "qualification"
        if name == "qualification.html"
        else "sessions"
    )
    return templates.TemplateResponse(request, name, {"nav": NAV, "section": section, **context})


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
def index(request: Request):
    from apps.api.main import list_sessions

    return render(
        request,
        "index.html",
        sessions=[_session_state(item["session_id"]) for item in list_sessions()["sessions"]],
    )


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request):
    from apps.api.main import list_profiles

    return render(request, "upload.html", profiles=list_profiles()["profiles"])


@router.post("/upload")
async def upload_submit(
    video: UploadFile = File(...),
    notes: str = Form(""),
    profile_id: str | None = Form(None),
    profile_revision: int | None = Form(None),
    source_kind: str = Form("unknown"),
    capture_attested_by: str = Form(""),
    session_name: str = Form(""),
):
    from apps.api.main import upload

    result = await upload(
        video=video,
        notes=notes,
        profile_id=profile_id or None,
        profile_revision=profile_revision,
        experiment_id="EXP-UPLOAD",
        controller_label="unspecified",
        policy_label="unspecified",
        protocol_id="camera-v2-unqualified",
        source_kind=source_kind,
        capture_attested_by=capture_attested_by,
        session_name=session_name,
    )
    return {"redirect": f"/ui/sessions/{result['session_id']}"}


@router.get("/profiles", response_class=HTMLResponse)
def profiles_page(request: Request):
    from apps.api.main import list_profiles

    return render(
        request,
        "profiles.html",
        profiles=list_profiles()["profiles"],
        allow_synthetic=os.environ.get("SIGNOFF_ALLOW_SYNTHETIC") == "1",
    )


@router.get("/live", response_class=HTMLResponse)
def live_page(request: Request):
    from apps.api.main import list_profiles

    return render(
        request,
        "live.html",
        profiles=list_profiles()["profiles"],
        allow_synthetic=os.environ.get("SIGNOFF_ALLOW_SYNTHETIC") == "1",
        selected_profile=request.query_params.get("profile"),
    )


@router.get("/sessions/{sid}", response_class=HTMLResponse)
def session_detail(request: Request, sid: str):
    from apps.api.main import _analysis_cache, latest_run, report, review_list

    state = _session_state(sid)
    analysis, report_md, reviews = None, "", {"items": []}
    try:
        analysis = _analysis_cache(sid)
        report_md = report(sid)
        reviews = review_list(sid)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
    run_id = latest_run(sid)
    duration = float((analysis or {}).get("video_duration_ms", 0) or 0)
    profile = (analysis or {}).get("provenance", {}).get("profile") or state.get("profile")
    cycles = []
    for row in reviews["items"]:
        item = dict(row)
        item["timeline_left"] = (
            min(100, max(0, item["start_ms"] / duration * 100)) if duration else 0
        )
        item["timeline_width"] = (
            min(
                100 - item["timeline_left"],
                max(0, (item["end_ms"] - item["start_ms"]) / duration * 100),
            )
            if duration
            else 0
        )
        cycles.append(item)
    from apps.api.main import run_status

    frozen_review = run_status(run_id).get("annotation_revision") if run_id else None
    stale = bool(run_id and reviews.get("revision_id") != frozen_review)
    workbench = {
        "session_id": sid,
        "run_id": run_id,
        "cycles": cycles,
        "duration_ms": duration,
        "profile": profile,
        "latest_review_revision": reviews.get("revision_id"),
    }
    return render(
        request,
        "session.html",
        s=state,
        analysis=analysis,
        metrics=analysis.get("metrics") if analysis else None,
        detected=cycles,
        report_md=report_md,
        run_id=run_id,
        profile=profile,
        duration_ms=duration,
        stale_report=stale,
        workbench_data=workbench,
    )


@router.get("/references/{sid}", response_class=HTMLResponse)
def reference_page(request: Request, sid: str):
    """Blinded workspace: metadata only, never predictions or outcome reviews."""
    from apps.api.main import session_info

    state = session_info(sid)
    state.update(source_labels(state))
    return render(request, "reference.html", s=state, reference_data={"session_id": sid})


@router.get("/qualification", response_class=HTMLResponse)
def qualification_page(request: Request):
    from apps.api.main import list_profiles, list_sessions

    sessions = [{**item, **source_labels(item)} for item in list_sessions()["sessions"]]
    return render(
        request,
        "qualification.html",
        sessions=sessions,
        profiles=list_profiles()["profiles"],
        qualification_data={"sessions": sessions},
    )


@router.post("/sessions/{sid}/review")
def ui_review(sid: str, payload: dict = Body(...)):
    from apps.api.main import review_submit

    return review_submit(sid, body=payload)


@router.post("/sessions/{sid}/feedback")
def feedback(sid: str, payload: dict = Body(...)):
    from apps.api.main import session_path

    session_path(sid)
    rating = payload.get("rating")
    if isinstance(rating, bool):
        raise HTTPException(422, "rating must be an integer 1-5")
    try:
        raw_rating = rating
        rating = int(rating)
        if float(raw_rating) != rating or not 1 <= rating <= 5:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "rating must be an integer 1-5") from exc
    entry = {
        "ts": utcnow(),
        "session": sid,
        "author": str(payload.get("author", "owner")),
        "rating": rating,
        "comment": str(payload.get("comment", "")),
        "pilot_type": "owner",
    }
    out = _data_dir() / "feedback" / f"{sid}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return {"status": "recorded", "file": str(out), "rating": rating}


@router.get("/sessions/{sid}/feedback")
def feedback_list(sid: str):
    from apps.api.main import session_path

    session_path(sid)
    path = _data_dir() / "feedback" / f"{sid}.jsonl"
    return {
        "session": sid,
        "entries": [json.loads(line) for line in path.read_text().splitlines() if line]
        if path.exists()
        else [],
    }


@router.get("/sessions/{sid}/agreement")
def agreement(sid: str):
    from apps.api.main import session_path
    from packages.domain.agreement import agreement_report
    from packages.domain.annotations import read_annotations

    root = session_path(sid)
    by_annotator: dict[str, list[dict]] = {}
    for path in sorted((root / "reviews").glob("*.json")):
        revision = json.loads(path.read_text())
        previous = {row["cycle"]: row for row in by_annotator.get(revision["annotator"], [])}
        previous.update({row["cycle"]: row for row in revision["cycles"]})
        by_annotator[revision["annotator"]] = list(previous.values())
    for path in sorted((root / "annotations").glob(f"{sid}_*.jsonl")):
        by_annotator.setdefault(path.stem.removeprefix(f"{sid}_"), read_annotations(path))
    if not by_annotator:
        raise HTTPException(404, "no annotations for this session")
    return agreement_report(by_annotator)


@router.post("/sessions/{sid}/acceptance", status_code=201)
def owner_acceptance(sid: str, payload: dict = Body(...)):
    from apps.api.main import identifier, jobs, run_status, session_path

    run_id = identifier(payload.get("run_id", ""))
    reviewer = identifier(payload.get("reviewer", ""))
    decision, reason = payload.get("decision"), str(payload.get("reason", "")).strip()
    if decision not in {"ACCEPT", "HOLD", "REJECT"} or not reason:
        raise HTTPException(422, "decision and non-empty reason are required")
    if run_status(run_id)["session_id"] != sid:
        raise HTTPException(422, "run does not belong to session")
    try:
        report_path = jobs().artifact(run_id, "report.json")
        report = json.loads(report_path.read_text())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    from packages.domain.ids import make_id

    acceptance = {
        "schema_version": 2,
        "acceptance_id": make_id("acc"),
        "pilot_type": "owner",
        "reviewer_kind": "OWNER",
        "reviewer": reviewer,
        "decision": decision,
        "reason": reason,
        "run_id": run_id,
        "report_hash": report["report_hash"],
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "created_at": utcnow(),
        "qualification_claim": "owner workflow acceptance; physical accuracy remains separate",
    }
    if payload.get("qualification_id"):
        if payload.get("inactivity_acknowledged") is not True:
            raise HTTPException(
                422, "review the saved inactivity evidence before binding a decision"
            )
        from apps.api.evidence import qualification_bindings

        try:
            bindings = qualification_bindings(_data_dir(), payload["qualification_id"], sid, run_id)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(422, str(exc)) from exc
        for field in (
            "qualification_id",
            "qualification_sha256",
            "manifest_sha256",
            "inactivity_review_hash",
        ):
            if bindings.get(field) is not None:
                if field.endswith("_sha256") or field.endswith("_hash"):
                    if not isinstance(bindings[field], str) or not re.fullmatch(
                        r"[a-f0-9]{64}", bindings[field]
                    ):
                        raise HTTPException(
                            422, "selected evidence binding contains an invalid digest"
                        )
                acceptance[field] = bindings[field]
        acceptance["inactivity_acknowledged"] = payload.get("inactivity_acknowledged") is True
    for field in ("qualification_sha256", "manifest_sha256"):
        value = payload.get(field)
        if value is not None:
            if payload.get("qualification_id") and value != acceptance.get(field):
                raise HTTPException(422, "supplied hash differs from the selected evidence binding")
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise HTTPException(422, f"{field} must be a SHA-256 digest")
            acceptance[field] = value
    atomic_json(
        session_path(sid) / "acceptance" / f"{acceptance['acceptance_id']}.json", acceptance
    )
    acceptance["download_url"] = f"/ui/sessions/{sid}/acceptance/{acceptance['acceptance_id']}"
    return acceptance


@router.get("/sessions/{sid}/acceptance/{acceptance_id}")
def acceptance_receipt(sid: str, acceptance_id: str):
    from fastapi.responses import FileResponse

    from apps.api.main import identifier, session_path

    path = session_path(sid) / "acceptance" / f"{identifier(acceptance_id)}.json"
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "owner decision receipt not found")
    return FileResponse(path, media_type="application/json", filename=path.name)
