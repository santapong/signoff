"""Server-rendered web UI (Phase 6 MVP): sessions, reports, review, upload.

Jinja2 templates + vanilla assets; no Node build step. State comes from the
datasets/ tree only.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

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
