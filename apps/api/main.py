"""Camera measurement API: immutable profiles, durable runs and review evidence."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import secrets
import threading
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from packages.domain.ids import make_id
from services.analysis_worker.jobs import (
    JobStore,
    atomic_json,
    latest_reviews,
    sha256,
    utcnow,
    verified_hash,
)

DATA_DIR = Path(os.environ.get("SIGNOFF_DATA_DIR", "datasets"))
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_stores: dict[str, JobStore] = {}
_store_lock = threading.Lock()
_review_lock = threading.Lock()


def identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise HTTPException(422, "invalid identifier")
    return value


def session_path(sid: str) -> Path:
    path = DATA_DIR / "sessions" / identifier(sid)
    if (
        not (path / "session.json").is_file()
        or path.is_symlink()
        or not path.resolve().is_relative_to(DATA_DIR.resolve())
    ):
        raise HTTPException(404, "unknown session")
    return path


def session_info(sid: str) -> dict:
    root = session_path(sid)
    result = json.loads((root / "session.json").read_text())
    revisions = sorted((root / "experiment-revisions").glob("*.json"))
    if revisions:
        result.update(json.loads(revisions[-1].read_text()))
    return result


def jobs() -> JobStore:
    key = str(DATA_DIR.resolve())
    with _store_lock:
        if key not in _stores:
            _stores[key] = JobStore(DATA_DIR)
        return _stores[key]


def profiles():
    from packages.domain.profiles import ProfileStore

    return ProfileStore(DATA_DIR / "profiles")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = jobs()
    from apps.api.evidence import shutdown_workspaces
    from apps.api.live import _stop_engine

    errors: list[BaseException] = []

    async def close_owned(function, *args, **kwargs):
        try:
            await asyncio.to_thread(function, *args, **kwargs)
        except BaseException as exc:
            errors.append(exc)

    # Register all resources before startup. Collect off-thread failures
    # explicitly: exception chaining alone can lose intermediate close errors.
    async with AsyncExitStack() as cleanup:
        cleanup.push_async_callback(close_owned, shutdown_workspaces, DATA_DIR, wait=True)
        cleanup.push_async_callback(close_owned, store.stop)
        cleanup.push_async_callback(close_owned, _stop_engine)
        try:
            store.start()
            yield
        except BaseException as exc:
            errors.insert(0, exc)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup("application lifecycle failures", errors)


app = FastAPI(title="Signoff Camera Measurement API", version="0.3.0-rc.1", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static", check_dir=False),
          name="static")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    from apps.api.auth import check_csrf, check_request

    path = request.url.path
    open_paths = {"/health", "/ui/login", "/docs", "/openapi.json", "/redoc"}
    if path not in open_paths and not path.startswith("/static/"):
        try:
            check_request(request)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                csrf = request.headers.get("x-csrf-token")
                check_csrf(request, csrf)
        except HTTPException as exc:
            if exc.status_code == 401 and path.startswith("/ui"):
                return RedirectResponse("/ui/login", status_code=303)
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    response = await call_next(request)
    if path.startswith("/ui") and not request.cookies.get("signoff_csrf"):
        response.set_cookie(
            "signoff_csrf",
            secrets.token_urlsafe(24),
            samesite="strict",
            secure=request.url.scheme == "https",
        )
    return response


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.3.0-rc.1", "data_dir": str(DATA_DIR)}


@app.get("/profiles")
def list_profiles() -> dict:
    return {"profiles": profiles().list()}


@app.post("/profiles", status_code=201)
def put_profile(body: dict = Body(...)) -> dict:
    try:
        return profiles().put(body)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/profiles/{profile_id}")
def get_profile(profile_id: str, revision: int | None = None) -> dict:
    try:
        return profiles().get(identifier(profile_id), revision)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(404, "profile revision not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/sessions")
def list_sessions() -> dict:
    root = DATA_DIR / "sessions"
    out = []
    if root.exists():
        for path in sorted(root.iterdir()):
            if (
                not _ID.fullmatch(path.name)
                or path.is_symlink()
                or not (path / "session.json").is_file()
            ):
                continue
            item = json.loads((path / "session.json").read_text())
            item["analyzed"] = (path / "latest_run.json").exists()
            out.append(item)
    return {"sessions": out}


def _looks_like_video(path: Path) -> bool:
    try:
        import cv2

        cap = cv2.VideoCapture(str(path))
        try:
            ok, _ = cap.read()
            return bool(cap.isOpened() and ok)
        finally:
            cap.release()
    except Exception:
        return False


def capture_metadata(source_kind: str, capture_attested_by: str = "", session_name: str = ""):
    if not isinstance(source_kind, str) or source_kind not in {
                           "physical_robot_station", "manual_tabletop_rehearsal",
                           "public_robot_video", "simulation", "unknown"}:
        raise HTTPException(422, "choose a supported source kind")
    if (not isinstance(capture_attested_by, str) or len(capture_attested_by) > 120
            or not isinstance(session_name, str) or len(session_name) > 120):
        raise HTTPException(422, "capture owner and session name must be text of at most "
                            "120 characters")
    if source_kind == "physical_robot_station" and not capture_attested_by.strip():
        raise HTTPException(422, "physical footage requires a named capture owner attestation")
    return {"source_kind": source_kind, "capture_attested_by": capture_attested_by.strip(),
            "name": session_name.strip(), "physical_capture_verified": False}


@app.post("/sessions/upload", status_code=201)
async def upload(
    video: UploadFile = File(...),
    notes: str = Form(""),
    profile_id: str | None = Form(None),
    profile_revision: int | None = Form(None),
    experiment_id: str = Form("EXP-UPLOAD"),
    controller_label: str = Form("unspecified"),
    policy_label: str = Form("unspecified"),
    protocol_id: str = Form("camera-v2-unqualified"),
    source_kind: str = Form("unknown"),
    capture_attested_by: str = Form(""),
    session_name: str = Form(""),
) -> dict:
    metadata = capture_metadata(source_kind, capture_attested_by, session_name)
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mkv"}:
        raise HTTPException(415, "supported media extensions are MP4 and MKV")
    labels = experiment_labels(
        {
            "experiment_id": experiment_id,
            "controller_label": controller_label,
            "policy_label": policy_label,
            "protocol_id": protocol_id,
        }
    )
    profile = get_profile(profile_id, profile_revision) if profile_id else None
    sid = make_id("ses")
    session_dir = DATA_DIR / "sessions" / sid
    video_dir = session_dir / "video"
    video_dir.mkdir(parents=True)
    dest = video_dir / f"source{suffix}"
    size = 0
    max_bytes = int(os.environ.get("SIGNOFF_UPLOAD_MAX_BYTES", str(2 * 1024**3)))
    try:
        with dest.open("xb") as handle:
            while chunk := await video.read(1 << 20):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, "upload exceeds configured size limit")
                handle.write(chunk)
        if not _looks_like_video(dest):
            raise HTTPException(415, "uploaded file is not a readable video")
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    session = {
        "schema_version": 2,
        "session_id": sid,
        **labels,
        **metadata,
        "video_file": dest.name,
        "video_sha256": sha256(dest),
        "notes": notes,
        "source": "recorded_upload",
        "created_at": utcnow(),
        "profile_id": profile.get("profile_id", profile.get("id")) if profile else None,
        "profile_revision": profile.get("revision") if profile else None,
        "profile": profile,
        "qualification": "unqualified",
    }
    atomic_json(session_dir / "session.json", session)
    return {**session, "file": dest.name, "sha256": session["video_sha256"]}


def experiment_labels(body: dict) -> dict:
    labels = {}
    for key in ("experiment_id", "controller_label", "policy_label", "protocol_id"):
        value = body.get(key)
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120:
            raise HTTPException(422, f"{key} must contain 1–120 characters")
        labels[key] = value.strip()
    identifier(labels["experiment_id"])
    identifier(labels["protocol_id"])
    return labels


@app.post("/sessions/{sid}/experiment", status_code=201)
def bind_experiment(sid: str, body: dict) -> dict:
    root = session_path(sid)
    labels = experiment_labels(body)
    revision = make_id("expmeta")
    value = {
        **labels,
        "experiment_metadata_revision": revision,
        "experiment_metadata_created_at": utcnow(),
    }
    atomic_json(root / "experiment-revisions" / f"{revision}.json", value)
    return value


@app.get("/sessions/{sid}/experiment")
def get_experiment(sid: str) -> dict:
    item = session_info(sid)
    return {
        key: item.get(key)
        for key in (
            "experiment_id",
            "controller_label",
            "policy_label",
            "protocol_id",
            "experiment_metadata_revision",
        )
    }


@app.post("/sessions/{sid}/analyze", status_code=202)
def analyze(sid: str, body: dict | None = Body(None)) -> dict:
    session = session_info(sid)
    if session.get("recording_state") == "running":
        raise HTTPException(409, "stop capture before immutable analysis")
    if not session.get("video_file"):
        raise HTTPException(422, "session has no recorded video")
    body = body or {}
    profile = session.get("profile")
    if body.get("profile_id"):
        profile = get_profile(body["profile_id"], body.get("profile_revision"))
    elif body.get("profile_revision") is not None:
        raise HTTPException(422, "profile_revision requires profile_id")
    try:
        item = jobs().enqueue(session, profile)
    except ValueError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {
        key: item[key] for key in ("schema_version", "run_id", "session_id", "state", "status_url")
    }


@app.get("/runs/{run_id}")
def run_status(run_id: str) -> dict:
    try:
        item = jobs().get(identifier(run_id))
    except FileNotFoundError as exc:
        raise HTTPException(404, "run not found") from exc
    return {
        key: value
        for key, value in item.items()
        if key not in {"session", "profile", "reviewed_cycles"}
    }


@app.get("/sessions/{sid}/runs")
def session_runs(sid: str) -> dict:
    session_path(sid)
    rows = [run_status(row["run_id"]) for row in jobs().session_runs(sid)]
    return {"session_id": sid, "runs": rows,
            "active_run": next((row for row in rows
                                if row["state"] in {"queued", "running"}), None),
            "latest_complete_run": latest_run(sid)}


@app.get("/runs/{run_id}/artifacts/{name}")
def run_artifact(run_id: str, name: str):
    try:
        path = jobs().artifact(identifier(run_id), name)
    except FileNotFoundError as exc:
        raise HTTPException(404, "artifact not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    media_type = {
        ".html": "text/html",
        ".json": "application/json",
        ".csv": "text/csv",
        ".md": "text/markdown",
        ".mp4": "video/mp4",
    }.get(path.suffix)
    return FileResponse(path, media_type=media_type)


def latest_run(sid: str) -> str | None:
    path = session_path(sid) / "latest_run.json"
    return json.loads(path.read_text())["run_id"] if path.exists() else None


def _analysis_cache(sid: str) -> dict:
    run_id = latest_run(sid)
    if run_id:
        try:
            return json.loads(jobs().artifact(run_id, "analysis.json").read_text())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    path = session_path(sid) / "analysis.json"
    if path.is_file():
        return {**json.loads(path.read_text()), "legacy": True}
    raise HTTPException(404, "run analyze first")


@app.get("/sessions/{sid}/report", response_class=PlainTextResponse)
def report(sid: str, run_id: str | None = None) -> str:
    selected = run_id or latest_run(sid)
    if selected:
        if run_status(identifier(selected))["session_id"] != sid:
            raise HTTPException(404, "run does not belong to session")
        try:
            return jobs().artifact(selected, "report.md").read_text()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    path = DATA_DIR / "reports" / identifier(sid) / "report.md"
    if not path.is_file():
        raise HTTPException(404, "report not found; run analyze first")
    return path.read_text()


@app.get("/sessions/{sid}/evidence")
def evidence(sid: str):
    item = session_info(sid)
    name = item.get("video_file")
    if not name or Path(name).name != name or Path(name).suffix not in {".mp4", ".mkv"}:
        raise HTTPException(404, "playable session video unavailable")
    path = session_path(sid) / "video" / name
    if path.is_symlink() or not path.is_file() or verified_hash(path) != item["video_sha256"]:
        raise HTTPException(409, "source evidence integrity check failed")
    return FileResponse(
        path, media_type="video/mp4" if path.suffix == ".mp4" else "video/x-matroska"
    )


@app.get("/sessions/{sid}/review")
def review_list(sid: str) -> dict:
    analysis = _analysis_cache(sid)
    rows, revision = latest_reviews(session_path(sid))
    ids = {cycle.get("cycle_id") for cycle in analysis.get("detected_cycles", [])}
    by_cycle = {
        row["cycle"]: row for row in rows if row.get("cycle_id") in ids or not row.get("cycle_id")
    }
    items = [
        {
            **cycle,
            "review": by_cycle.get(cycle["cycle"]),
            "needs_review": cycle["cycle"] not in by_cycle,
        }
        for cycle in analysis.get("detected_cycles", [])
    ]
    return {
        "session_id": sid,
        "run_id": analysis.get("run_id"),
        "items": items,
        "revision_id": revision,
        "n_needing_review": sum(item["needs_review"] for item in items),
    }


@app.post("/sessions/{sid}/review")
def review_submit(sid: str, body: dict[str, Any]) -> dict:
    sdir = session_path(sid)
    analysis = _analysis_cache(sid)
    if body.get("run_id") and body["run_id"] != analysis.get("run_id"):
        raise HTTPException(409, "review run is no longer the selected session run")
    annotator = identifier(body.get("annotator", "reviewer"))
    rows = body.get("cycles")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(422, "cycles must be a non-empty list")
    detected = {row["cycle"]: row for row in analysis.get("detected_cycles", [])}
    validated = []
    for row in rows:
        try:
            if isinstance(row["cycle"], bool):
                raise ValueError("cycle must be a positive integer")
            cycle = int(row["cycle"])
            start, end = float(row["start_ms"]), float(row["end_ms"])
            outcome = row.get("outcome", "UNKNOWN")
            failure = row.get("failure_type", "NONE")
            notes = row.get("notes", "")
            if not isinstance(notes, str) or len(notes) > 2000:
                raise ValueError("notes must be text of at most 2000 characters")
            if float(row["cycle"]) != cycle:
                raise ValueError("cycle must be an integer")
            if cycle not in detected:
                raise ValueError("cycle is not present in the selected run")
            if not all(math.isfinite(value) for value in (start, end)) or not 0 <= start < end:
                raise ValueError("cycle boundaries must be finite and ordered")
            if end > analysis["video_duration_ms"] + 1:
                raise ValueError("cycle end exceeds observed duration")
            if outcome not in {"OK", "FAILED", "UNKNOWN"}:
                raise ValueError("outcome must be OK, FAILED or UNKNOWN")
            if failure not in {
                "NONE",
                "GRASP_MISS",
                "OBJECT_SLIP",
                "OBJECT_DROP",
                "DESTINATION_OCCUPIED",
                "INTERRUPTED",
                "OTHER",
            }:
                raise ValueError("unknown failure type")
            if outcome == "FAILED" and failure == "NONE":
                raise ValueError("FAILED outcome requires a failure type")
            validated.append(
                {
                    "cycle": cycle,
                    "start_ms": start,
                    "end_ms": end,
                    "cycle_start_ms": start,
                    "cycle_end_ms": end,
                    "outcome": outcome,
                    "failure_type": failure,
                    "notes": notes,
                    "annotator": annotator,
                    "session": sid,
                    "detected_cycle_id": detected[cycle].get("cycle_id"),
                    "cycle_id": detected[cycle].get("cycle_id"),
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
    with _review_lock:
        revision = make_id("rev")
        path = sdir / "reviews" / f"{revision}.json"
        item = {
            "schema_version": 2,
            "revision_id": revision,
            "created_at": utcnow(),
            "run_id": analysis.get("run_id"),
            "annotator": annotator,
            "cycles": validated,
        }
        if path.exists():
            raise HTTPException(409, "review revision collision; retry request")
        atomic_json(path, item)
    return {
        "session_id": sid,
        "written": len(validated),
        "revision_id": revision,
        "file": str(path),
        "annotator": annotator,
        "report_update": "create a new analysis run to freeze this review revision",
    }


from apps.api.evidence import router as evidence_router  # noqa: E402
from apps.api.live import router as live_router  # noqa: E402
from apps.api.ui import router as ui_router  # noqa: E402

app.include_router(live_router)
app.include_router(ui_router)
app.include_router(evidence_router)
