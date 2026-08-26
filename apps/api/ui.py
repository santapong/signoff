"""Server-rendered web UI (Phase 6 MVP): sessions, reports, review, upload.

Jinja2 templates + vanilla assets; no Node build step. State comes from the
datasets/ tree only.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

LOGIN_HTML = """<!DOCTYPE html>
<html><head><title>Signoff — Login</title>
<style>body{font-family:system-ui;margin:4rem;max-width:360px}
input{width:100%;padding:.5rem;margin:.4rem 0}
button{padding:.5rem 1.2rem}</style></head><body>
<h1>Pilot sign-in</h1>
<form method="post" action="/ui/login">
 <input type="password" name="token" placeholder="access token" required>
 <button type="submit">Sign in</button>
</form></body></html>"""


@router.get("/login", response_class=HTMLResponse)
def login_form():
    return HTMLResponse(LOGIN_HTML)


@router.post("/login")
def login_submit(token: str = Form(...)):
    import os
    from apps.api.auth import token_required
    if token_required() and token != os.environ.get("SIGNOFF_TOKEN"):
        return HTMLResponse("<p>Invalid token.</p>" + LOGIN_HTML,
                            status_code=401)
    resp = RedirectResponse("/ui/", status_code=303)
    resp.set_cookie("signoff_session", token, httponly=True)
    return resp

def _data_dir() -> Path:
    """Resolve at call time so test env-var reloads take effect."""
    import apps.api.main as api_main
    return api_main.DATA_DIR


NAV = """
<nav class="nav">
  <a href="/ui">Sessions</a>
  <a href="/ui/upload">Upload</a>
  <a href="/live/" target="_blank">Live monitor</a>
  <a href="/docs" target="_blank">API</a>
</nav>
"""


def _session_ids() -> list[str]:
    root = _data_dir() / "sessions"
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _session_state(sid: str) -> dict:
    d = _data_dir() / "sessions" / sid
    info: dict = {"id": sid}
    sj = d / "session.json"
    if sj.exists():
        meta = json.loads(sj.read_text())
        info["notes"] = meta.get("notes", "")
        info["created"] = meta.get("created", sid)
    else:
        info["notes"] = ""
        info["created"] = sid
    info["analyzed"] = (d / "analysis.json").exists()
    report = _data_dir() / "reports" / sid / "metrics.json"
    if report.exists():
        m = json.loads(report.read_text())
        det = m.get("detection", {})
        info["f1"] = det.get("f1") if det.get("available") else None
        info["cycles"] = m.get("cycle_durations", {}).get("n", 0)
        info["success_rate"] = m.get("outcomes", {}).get("success_rate")
    else:
        info["f1"] = info["cycles"] = info["success_rate"] = None
    return info


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    sessions = [_session_state(s) for s in _session_ids()]
    return templates.TemplateResponse(request, "index.html", {
        "nav": NAV, "sessions": sessions})


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request):
    return templates.TemplateResponse(request, "upload.html", {"nav": NAV})


@router.post("/upload")
async def upload_submit(video: UploadFile = File(...), notes: str = ""):
    # reuse the JSON API handler directly
    from apps.api.main import upload as api_upload
    try:
        result = await api_upload(video=video, notes=notes)
    except HTTPException as exc:
        return HTMLResponse(f"<h1>Upload failed</h1><p>{exc.detail}</p>"
                            f"{NAV}<p><a href='/ui/upload'>Try again</a></p>",
                            status_code=exc.status_code)
    return RedirectResponse(f"/ui/sessions/{result['session_id']}",
                            status_code=303)


@router.get("/sessions/{sid}", response_class=HTMLResponse)
def session_detail(request: Request, sid: str):
    sdir = _data_dir() / "sessions" / sid
    if not sdir.exists():
        raise HTTPException(404, "unknown session")
    state = _session_state(sid)

    analysis_path = sdir / "analysis.json"
    analysis = (json.loads(analysis_path.read_text())
                if analysis_path.exists() else None)
    hypotheses = analysis.get("failure_hypotheses", []) if analysis else []
    for h in hypotheses:
        h["needs_review"] = h.get("confidence", 0) < 0.75

    report_md = ""
    rp = _data_dir() / "reports" / sid / "report.md"
    if rp.exists():
        report_md = rp.read_text()

    metrics = analysis.get("metrics") if analysis else None
    detected = analysis.get("detected_cycles", []) if analysis else []

    return templates.TemplateResponse(request, "session.html", {
        "nav": NAV, "s": state, "metrics": metrics,
        "hypotheses": hypotheses, "report_md": report_md,
        "detected": detected})


from fastapi import Body  # noqa: E402


@router.post("/sessions/{sid}/review")
def ui_review(sid: str, payload: dict = Body(...)):
    """Browser form fallback for the human-review loop."""
    from apps.api.main import review_submit
    result = review_submit(sid, body=payload)
    return RedirectResponse(f"/ui/sessions/{sid}", status_code=303)


# ---- pilot feedback (AC4) ----------------------------------------------

@router.post("/sessions/{sid}/feedback")
def feedback(sid: str, payload: dict = Body(...)):
    sdir = _data_dir() / "sessions" / sid
    if not sdir.exists():
        raise HTTPException(404, f"unknown session {sid}")
    rating = payload.get("rating")
    try:
        rating = int(rating)
        assert 1 <= rating <= 5
    except (TypeError, ValueError, AssertionError):
        raise HTTPException(422, "rating must be an integer 1-5")
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session": sid,
        "author": str(payload.get("author", "pilot")),
        "rating": rating,
        "comment": str(payload.get("comment", "")),
    }
    out = _data_dir() / "feedback" / f"{sid}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return {"status": "recorded", "file": str(out), "rating": rating}


@router.get("/sessions/{sid}/feedback")
def feedback_list(sid: str):
    path = _data_dir() / "feedback" / f"{sid}.jsonl"
    entries = []
    if path.exists():
        entries = [json.loads(l) for l in path.read_text().splitlines() if l]
    return {"session": sid, "entries": entries}


# ---- E8 agreement endpoint (AC2) ----------------------------------------

@router.get("/sessions/{sid}/agreement")
def agreement(sid: str):
    from packages.domain.agreement import agreement_report
    from packages.domain.annotations import read_annotations
    adir = _data_dir() / "sessions" / sid / "annotations"
    by_annotator: dict[str, list[dict]] = {}
    if adir.exists():
        for f in sorted(adir.glob(f"{sid}_*.jsonl")):
            annotator = f.stem.removeprefix(f"{sid}_")
            by_annotator[annotator] = read_annotations(f)
    if not by_annotator:
        raise HTTPException(404, "no annotations for this session")
    return agreement_report(by_annotator)
