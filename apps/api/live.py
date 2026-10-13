"""Explicit live source control and bounded authenticated event delivery."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections import deque

from fastapi import APIRouter, Body, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response

from packages.domain.ids import make_id
from services.analysis_worker.jobs import atomic_json, sha256, utcnow
from services.live_worker.engine import LATENCY_BUDGET_MS, EventBus, LiveEngine, make_source

router = APIRouter(prefix="/live", tags=["live"])
_state: dict = {"engine": None, "lock": threading.Lock(), "device": None}


def get_engine() -> LiveEngine | None:
    return _state["engine"]


def _start_engine(
    device_index: int | None, synthetic: bool, profile: dict, session_id: str, session_dir
) -> dict:
    with _state["lock"]:
        if _state["engine"] is not None:
            raise HTTPException(409, "a live capture already owns the source")
        bus = EventBus()
        source = make_source(
            device_index,
            synthetic=synthetic,
            **{key: profile["camera"][key] for key in ("width", "height", "fps")},
        )
        try:
            engine = LiveEngine(
                source, bus, profile=profile, session_id=session_id, session_dir=session_dir
            )
            engine.start()
        except Exception:
            source.close()
            raise
        _state.update(
            engine=engine,
            bus=bus,
            device=device_index,
            synthetic=synthetic,
            session_id=session_id,
            session_dir=session_dir,
        )
        return {
            "status": "started",
            "session_id": session_id,
            "source": "synthetic_development" if synthetic else "usb_camera",
            "synthetic": synthetic,
            "latency_budget_ms": LATENCY_BUDGET_MS,
        }


def _stop_engine() -> dict:
    with _state["lock"]:
        engine = _state["engine"]
        if engine is None:
            return {"status": "not_running"}
        engine.stop()
        engine.source.close()
        if engine._thread and engine._thread.is_alive():
            raise HTTPException(503, "capture stop is pending; source ownership is retained")
        _state["engine"] = None
        session_dir = _state["session_dir"]
        path = session_dir / "session.json"
        session = json.loads(path.read_text())
        manifest = session_dir / "video" / "recording.manifest.json"
        snapshot = engine.snapshot()
        session.update(
            recording_state="stopped"
            if snapshot.get("recording_state") == "complete"
            else "interrupted",
            stopped_at=utcnow(),
            quality_flags=snapshot.get("quality_flags", []),
            recording_error=snapshot.get("recording_error"),
        )
        if manifest.is_file():
            session.update(video_file=manifest.name, video_sha256=sha256(manifest))
        atomic_json(path, session)
        return {"status": "stopped", "session_id": _state["session_id"]}


@router.post("/start")
def live_start(
    request: Request,
    body: dict | None = Body(None),
    synthetic: bool | None = None,
    device: int | None = None,
) -> dict:
    from apps.api.main import DATA_DIR, get_profile

    body = body or {}
    if "source" in body and (synthetic is not None or device is not None):
        raise HTTPException(422, "cannot combine legacy query source and JSON source")
    if not body.get("profile_id"):
        raise HTTPException(422, "an explicit profile_id is required")
    source = body.get("source")
    if source is None and synthetic is not None:
        source = "synthetic" if synthetic else "usb"
    if source not in {"usb", "synthetic"}:
        raise HTTPException(422, "source must explicitly be usb or synthetic")
    development = source == "synthetic"
    if development and os.environ.get("SIGNOFF_ALLOW_SYNTHETIC") != "1":
        raise HTTPException(403, "synthetic input requires SIGNOFF_ALLOW_SYNTHETIC=1")
    device_index = body.get("device_index", device)
    if not development and (
        isinstance(device_index, bool)
        or not isinstance(device_index, int)
        or not 0 <= device_index <= 255
    ):
        raise HTTPException(422, "USB input requires an explicit device_index from 0 to 255")
    if body.get("record") is not True:
        raise HTTPException(422, "record=true is required for archived measurement capture")
    profile = get_profile(body["profile_id"], body.get("profile_revision"))
    if not development and device_index != profile["camera"]["device_index"]:
        raise HTTPException(422, "selected device differs from the frozen camera profile")
    sid = make_id("ses")
    session_dir = DATA_DIR / "sessions" / sid
    session = {
        "experiment_id": str(body.get("experiment_id", "EXP-LIVE")),
        "controller_label": str(body.get("controller_label", "unspecified")),
        "policy_label": str(body.get("policy_label", "unspecified")),
        "protocol_id": str(body.get("protocol_id", "camera-v2-unqualified")),
        "schema_version": 2,
        "session_id": sid,
        "source": "synthetic_development" if development else "usb_camera",
        "created_at": utcnow(),
        "recording_state": "running",
        "profile": profile,
        "profile_id": body["profile_id"],
        "profile_revision": profile["revision"],
        "notes": str(body.get("notes", "")),
        "qualification": "unqualified",
    }
    # Refuse a second owner before creating a spurious running session.
    if get_engine() is not None:
        raise HTTPException(409, "a live capture already owns the source")
    atomic_json(session_dir / "session.json", session)
    try:
        return _start_engine(device_index, development, profile, sid, session_dir)
    except HTTPException:
        session["recording_state"] = "failed"
        atomic_json(session_dir / "session.json", session)
        raise
    except (RuntimeError, OSError, ValueError) as exc:
        session.update(recording_state="failed", reason=str(exc))
        atomic_json(session_dir / "session.json", session)
        raise HTTPException(503, str(exc)) from exc


@router.post("/stop")
def live_stop() -> dict:
    return _stop_engine()


@router.get("/status")
def live_status() -> dict:
    engine = _state["engine"]
    if engine is None:
        return {"running": False, "session_id": _state.get("session_id")}
    snapshot = engine.snapshot()
    latencies = [event.latency_ms for event in _state["bus"].recent(100)]
    return {
        **snapshot,
        "running": snapshot["running"],
        "events": [event.to_dict() for event in _state["bus"].recent(20)],
        "session_id": _state["session_id"],
        "synthetic_device": _state["synthetic"],
        "source": "synthetic_development" if _state["synthetic"] else "usb_camera",
        "max_latency_ms": round(max(latencies), 1) if latencies else 0.0,
        "latency_budget_ms": LATENCY_BUDGET_MS,
    }


@router.get("/preview")
def live_preview():
    engine = get_engine()
    payload = engine.preview_jpeg() if engine and hasattr(engine, "preview_jpeg") else None
    if not payload:
        raise HTTPException(404, "no captured preview frame available")
    return Response(payload, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.websocket("/ws")
async def live_ws(ws: WebSocket):
    from apps.api.auth import check_websocket

    try:
        check_websocket(ws)
    except HTTPException:
        await ws.close(code=1008)
        return
    await ws.accept()
    bus = _state.get("bus") if get_engine() else None
    if bus is None:
        await ws.send_json({"type": "error", "message": "engine not running"})
        await ws.close()
        return
    buffered: deque = deque(maxlen=128)
    queue_lock = threading.Lock()
    dropped = 0

    def on_event(event):
        nonlocal dropped
        with queue_lock:
            if len(buffered) == buffered.maxlen:
                dropped += 1
            buffered.append(event.to_dict())

    bus.subscribe(on_event)
    try:
        while True:
            if get_engine() is None or _state.get("bus") is not bus:
                await ws.send_json({"type": "session_stopped"})
                await ws.close()
                break
            with queue_lock:
                event = buffered.popleft() if buffered else None
                lost, dropped = dropped, 0
            if lost:
                await ws.send_json(
                    {
                        "type": "subscriber_gap",
                        "dropped_events": lost,
                        "message": "refresh persistent status/evidence",
                    }
                )
            await ws.send_json(event or {"type": "heartbeat"})
            try:
                message = await asyncio.wait_for(ws.receive_text(), timeout=0.5)
                if message == "stop":
                    break
            except TimeoutError:
                continue
    except WebSocketDisconnect:
        pass
    finally:
        if hasattr(bus, "unsubscribe"):
            bus.unsubscribe(on_event)
        else:
            with bus._lock:
                if on_event in bus._subscribers:
                    bus._subscribers.remove(on_event)


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
def dashboard(request: Request):
    from apps.api.ui import live_page

    return live_page(request)
