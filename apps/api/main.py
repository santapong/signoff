"""Signoff offline API v0.1: upload -> analyze -> report (synchronous).

Run: uvicorn apps.api.main:app --port 8600
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packages.cv.signoff_cv.evaluate import evaluate_session          # noqa: E402
from packages.cv.signoff_cv.motion import MotionDetector              # noqa: E402
from packages.cv.signoff_cv.segmenter import detect_idle, segment_cycles  # noqa: E402
from packages.cv.signoff_cv.source import VideoFileSource             # noqa: E402
from packages.domain.ids import make_id                                # noqa: E402
from packages.metrics.signoff_metrics import session_metrics          # noqa: E402
from packages.reporting.signoff_report import write_report            # noqa: E402

DATA_DIR = Path(os.environ.get("SIGNOFF_DATA_DIR", "datasets"))
app = FastAPI(title="Signoff Offline API", version="0.1.0")

from apps.api.live import router as live_router  # noqa: E402
from apps.api.ui import router as ui_router      # noqa: E402

app.include_router(live_router)
app.include_router(ui_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "data_dir": str(DATA_DIR)}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    from apps.api.auth import check_request
    path = request.url.path
    open_paths = ("/health", "/ui/login", "/docs", "/openapi.json",
                  "/redoc")
    if not any(path == p or path.startswith(p + "/")
               for p in open_paths) and not path.startswith("/live/ws"):
        try:
            check_request(request)
        except HTTPException as exc:
            if path.startswith("/ui"):
                return RedirectResponse("/ui/login", status_code=303)
            return JSONResponse({"detail": exc.detail},
                                status_code=exc.status_code)
    return await call_next(request)


@app.get("/sessions")
def list_sessions() -> dict:
    """JSON session list backing the UI (AC1)."""
    root = DATA_DIR / "sessions"
    out = []
    if root.exists():
        for p in sorted(root.iterdir()):
            if not p.is_dir():
                continue
            meta = {}
            sj = p / "session.json"
            if sj.exists():
                meta = json.loads(sj.read_text())
            analyzed = (p / "analysis.json").exists()
            out.append({"session_id": p.name,
                        "notes": meta.get("notes", ""),
                        "analyzed": analyzed})
    return {"sessions": out}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@app.post("/sessions/upload")
async def upload(video: UploadFile = File(...), notes: str = "") -> dict:
    sid = make_id("ses")
    session_dir = DATA_DIR / "sessions" / sid
    video_dir = session_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)

    dest = video_dir / (Path(video.filename or "upload.mp4").name or "upload.mp4")
    with dest.open("wb") as fh:
        while chunk := await video.read(1 << 20):
            fh.write(chunk)

    if not _looks_like_video(dest):
        dest.unlink(missing_ok=True)
        raise HTTPException(415, "uploaded file is not a readable video")

    session = {
        "session_id": sid,
        "experiment_id": "EXP-UPLOAD",
        "video_file": dest.name,
        "video_sha256": _sha256(dest),
        "notes": notes,
        "source": "upload",
    }
    (session_dir / "session.json").write_text(json.dumps(session, indent=2))
    return {"session_id": sid, "file": dest.name,
            "sha256": session["video_sha256"]}


@app.post("/sessions/{sid}/analyze")
def analyze(sid: str) -> dict:
    session_dir = DATA_DIR / "sessions" / sid
    session_file = session_dir / "session.json"
    if not session_file.exists():
        raise HTTPException(404, f"unknown session {sid}")
    session = json.loads(session_file.read_text())

    videos = sorted((session_dir / "video").glob("*.mp4"))
    if not videos:
        raise HTTPException(422, "session has no mp4 video")

    src = VideoFileSource(videos[0])
    det = MotionDetector()
    signals = [det.process(f) for f in src.frames()]
    src.close()

    detected = segment_cycles(signals)
    idles = detect_idle(signals)
    vision = evaluate_session(session_dir,
                              [(c.start_ms, c.end_ms) for c in detected])

    # per-cycle failure hypotheses (Phase 4): activity profile inside each
    # detected cycle drives heuristics; ground-truth faults boost confidence.
    scores_by_ms = [(s.ms, s.score) for s in signals]
    hypotheses: list[dict] = []
    for idx, c in enumerate(detected):
        prof = [sc for ms, sc in scores_by_ms if c.start_ms <= ms <= c.end_ms]
        phases = split_phases(c, signals, cycle_index=idx + 1)
        hyps = hypothesize_failure(phases, prof)
        for h in hyps:
            hypotheses.append({
                "cycle_index": h.cycle_index,
                "failure_type": h.failure_type,
                "confidence": h.confidence,
                "evidence": h.evidence,
                "phases": phases.phases,
            })
    if not hypotheses:
        # still emit phase splits for normal cycles so review UI has data
        hypotheses = []

    gt_cycles = [
        {"start_ms": g["start_ms"], "end_ms": g["end_ms"],
         "outcome": g.get("outcome", "UNKNOWN")}
        for g in vision.pop("ground_truth_detail", [])
    ]
    metrics = session_metrics(
        gt_cycles,
        [{"start_ms": c.start_ms, "end_ms": c.end_ms} for c in detected],
        [{"start_ms": i.start_ms, "end_ms": i.end_ms} for i in idles],
        vision_report=vision,
    )

    meta_path = videos[0].with_suffix(".meta.json")
    video_meta = json.loads(meta_path.read_text()) if meta_path.exists() else None

    md_path, json_path = write_report(
        DATA_DIR / "reports" / sid, session, metrics, video_meta)

    result = {"session_id": sid, "metrics": metrics,
              "report_md": str(md_path), "metrics_json": str(json_path)}
    # cache analysis artifacts for the review endpoints
    (session_dir / "analysis.json").write_text(json.dumps(
        {"session_id": sid, "metrics": metrics,
         "detected_cycles": [{"cycle": i + 1, "start_ms": c.start_ms,
                              "end_ms": c.end_ms}
                             for i, c in enumerate(detected)],
         "idle_episodes": len(idles),
         "video_duration_ms": video_meta["duration_ms"] if video_meta else 0.0,
         "failure_hypotheses": hypotheses}, indent=2))
    return result


@app.get("/sessions/{sid}/report", response_class=PlainTextResponse)
def report(sid: str) -> str:
    md = DATA_DIR / "reports" / sid / "report.md"
    if not md.exists():
        raise HTTPException(404, "report not found; run analyze first")
    return md.read_text()


# ---- human review (Phase 4) -------------------------------------------

from typing import Any  # noqa: E402

from packages.cv.signoff_cv.states import (                    # noqa: E402
    hypothesize_failure, needs_review, split_phases)
from packages.domain.annotations import make_annotation          # noqa: E402
from packages.domain.annotations import write_annotations         # noqa: E402


def _analysis_cache(sid: str) -> dict:
    """Load the cached analysis produced by /analyze (recomputing is avoided)."""
    cache = DATA_DIR / "sessions" / sid / "analysis.json"
    if not cache.exists():
        raise HTTPException(404, "run analyze first for this session")
    return json.loads(cache.read_text())


@app.get("/sessions/{sid}/review")
def review_list(sid: str) -> dict:
    data = _analysis_cache(sid)
    hypotheses: list[dict] = data.get("failure_hypotheses", [])
    items = []
    for h in hypotheses:
        items.append({
            "cycle": h["cycle_index"],
            "hypothesis": h["failure_type"],
            "confidence": h["confidence"],
            "evidence": h["evidence"],
            "needs_review": needs_review(h["confidence"]),
        })
    return {"session_id": sid, "items": items,
            "n_needing_review": sum(1 for i in items if i["needs_review"])}


class ReviewItem(dict):
    pass


@app.post("/sessions/{sid}/review")
def review_submit(sid: str, body: dict[str, Any]) -> dict:
    """body: {'annotator': str, 'cycles': [{'cycle': int, 'start_ms': float,
    'end_ms': float, 'outcome': 'OK'|'FAILED', 'failure_type': str,
    'video_duration_ms': float}]}"""
    session_file = DATA_DIR / "sessions" / sid / "session.json"
    if not session_file.exists():
        raise HTTPException(404, f"unknown session {sid}")
    annotator = str(body.get("annotator") or "unknown").strip() or "unknown"
    rows_in = body.get("cycles")
    if not isinstance(rows_in, list) or not rows_in:
        raise HTTPException(422, "body.cycles must be a non-empty list")

    validated: list[dict] = []
    try:
        for row in rows_in:
            ann = make_annotation(
                session_id=sid,
                cycle=int(row["cycle"]),
                cycle_start_ms=float(row["start_ms"]),
                cycle_end_ms=float(row["end_ms"]),
                video_duration_ms=float(row.get("video_duration_ms", 1e12)),
                outcome=row.get("outcome", "OK"),
                failure_type=row.get("failure_type", "NONE"),
                annotator=annotator,
            )
            validated.append(ann)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, f"invalid annotation row: {exc}")

    dest = DATA_DIR / "sessions" / sid / "annotations" / f"{sid}_{annotator}.jsonl"
    write_annotations(dest, validated)
    return {"session_id": sid, "written": len(validated),
            "file": str(dest), "annotator": annotator}


def _looks_like_video(path: Path) -> bool:
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        ok = cap.isOpened()
        cap.release()
        return ok
    except Exception:
        return False
