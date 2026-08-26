"""Signoff offline API v0.1: upload -> analyze -> report (synchronous).

Run: uvicorn apps.api.main:app --port 8600
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "data_dir": str(DATA_DIR)}


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

    return {"session_id": sid, "metrics": metrics,
            "report_md": str(md_path), "metrics_json": str(json_path)}


@app.get("/sessions/{sid}/report", response_class=PlainTextResponse)
def report(sid: str) -> str:
    md = DATA_DIR / "reports" / sid / "report.md"
    if not md.exists():
        raise HTTPException(404, "report not found; run analyze first")
    return md.read_text()


def _looks_like_video(path: Path) -> bool:
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        ok = cap.isOpened()
        cap.release()
        return ok
    except Exception:
        return False
