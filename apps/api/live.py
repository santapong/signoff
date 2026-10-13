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
from services.live_worker.camera import discover_cameras
from services.live_worker.engine import LATENCY_BUDGET_MS, EventBus, LiveEngine, make_source

router = APIRouter(prefix="/live", tags=["live"])
_state: dict = {"engine": None, "lock": threading.RLock(), "device": None}


def get_engine() -> LiveEngine | None:
    return _state["engine"]


def _start_engine(
    device_index: int | None,
    synthetic: bool,
    profile: dict,
    session_id: str,
    session_dir,
    source_kind: str = "unknown",
    capture_attested_by: str | None = None,
) -> dict:
    with _state["lock"]:
        engine = get_engine()
        if engine is not None:
            if engine.mode != "preview":
                raise HTTPException(409, "a live capture already owns the source")
            if synthetic != _state["synthetic"] or device_index != _state["device"]:
                raise HTTPException(
                    409,
                    "Use the preview camera; close preview before changing sources.",
                )
            engine.begin_recording(
                profile, session_dir, session_id, source_kind, capture_attested_by
            )
            bus = _state["bus"]
        else:
            bus = EventBus()
            source = make_source(
                device_index,
                synthetic=synthetic,
                **{key: profile["camera"][key] for key in ("width", "height", "fps")},
            )
            try:
                engine = LiveEngine(
                    source,
                    bus,
                    profile=profile,
                    session_id=session_id,
                    session_dir=session_dir,
                    data_dir=session_dir,
                    source_kind=source_kind,
                    capture_attested_by=capture_attested_by,
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
            "mode": "recording",
            "session_id": session_id,
            "source": "synthetic_development" if synthetic else "usb_camera",
            "synthetic": synthetic,
            "latency_budget_ms": LATENCY_BUDGET_MS,
            "profile_id": profile["profile_id"],
            "profile_revision": profile["revision"],
            "profile_hash": profile["profile_hash"],
            "runtime_profile": engine.runtime_profile,
        }


def _stop_engine() -> dict:
    with _state["lock"]:
        engine = get_engine()
        if engine is None:
            return {"status": "not_running", "mode": "closed"}
        had_recording = bool(engine.session_dir)
        engine.stop()
        if engine._thread and engine._thread.is_alive():
            raise HTTPException(503, "capture stop is pending; source ownership is retained")
        _state["engine"] = None
        _state["last_snapshot"] = engine.snapshot()
        if not had_recording:
            return {"status": "stopped", "mode": "closed", "session_id": None}
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
        runtime_path, soak_path = (
            session_dir / "capture-profile.json",
            session_dir / "live-soak.json",
        )
        if runtime_path.is_file():
            session["runtime_profile_sha256"] = sha256(runtime_path)
        if soak_path.is_file():
            session["live_soak_sha256"] = sha256(soak_path)
            session["live_soak_file"] = soak_path.name
        atomic_json(path, session)
        return {
            "status": "stopped",
            "mode": "closed",
            "session_id": _state["session_id"],
            "soak_receipt_available": soak_path.is_file(),
        }


def _source_choice(
    body: dict, synthetic: bool | None = None, device: int | None = None
) -> tuple[int | None, bool]:
    if "source" in body and (synthetic is not None or device is not None):
        raise HTTPException(422, "cannot combine legacy query source and JSON source")
    source = body.get("source")
    if source is None and synthetic is not None:
        source = "synthetic" if synthetic else "usb"
    if not isinstance(source, str) or source not in {"usb", "synthetic"}:
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
    return None if development else device_index, development


@router.get("/cameras")
def list_cameras() -> dict:
    return {
        "cameras": discover_cameras(),
        "probing_required": True,
        "discovery_basis": "linux_video_metadata",
        "devices_opened": False,
        "numeric_fallback": True,
    }


@router.post("/preview/start")
def preview_start(body: dict = Body(...)) -> dict:
    from apps.api.main import DATA_DIR
    from packages.domain.profiles import validate_profile

    device_index, development = _source_choice(body)
    try:
        settings = validate_profile(
            {
                "profile_id": "preview-settings",
                "camera": {
                    "device_index": device_index or 0,
                    "width": body.get("width", 1280),
                    "height": body.get("height", 720),
                    "fps": body.get("fps", 30),
                },
                "detector": {"method": "motion"},
            }
        )["camera"]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with _state["lock"]:
        if get_engine() is not None:
            raise HTTPException(409, "a camera owner is already open; close or stop it explicitly")
        try:
            source = make_source(
                device_index,
                synthetic=development,
                **{key: settings[key] for key in ("width", "height", "fps")},
            )
            bus = EventBus()
            try:
                engine = LiveEngine(
                    source,
                    bus,
                    mode="preview",
                    data_dir=DATA_DIR,
                    poll_interval_s=1 / settings["fps"],
                )
                engine.start()
            except Exception:
                source.close()
                raise
        except (RuntimeError, OSError, ValueError) as exc:
            raise HTTPException(503, str(exc)) from exc
        _state.update(
            engine=engine,
            bus=bus,
            device=device_index,
            synthetic=development,
            session_id=None,
            session_dir=None,
        )
        return {
            "status": "started",
            "mode": "preview",
            "session_id": None,
            "source": "synthetic_development" if development else "usb_camera",
            "camera_open": True,
            "recording_state": "disabled",
        }


@router.post("/preview/stop")
def preview_stop() -> dict:
    with _state["lock"]:
        engine = get_engine()
        if engine is not None and engine.mode == "recording":
            raise HTTPException(409, "recording is active; use Stop and preserve recording")
        return _stop_engine()


@router.post("/start")
def live_start(
    request: Request,
    body: dict | None = Body(None),
    synthetic: bool | None = None,
    device: int | None = None,
) -> dict:
    from apps.api.main import DATA_DIR, capture_metadata, get_profile

    body = body or {}
    if not isinstance(body.get("profile_id"), str) or not body["profile_id"].strip():
        raise HTTPException(422, "an explicit profile_id is required")
    device_index, development = _source_choice(body, synthetic, device)
    if body.get("record") is not True:
        raise HTTPException(422, "record=true is required for archived measurement capture")
    profile = get_profile(body["profile_id"], body.get("profile_revision"))
    if not development and device_index != profile["camera"]["device_index"]:
        raise HTTPException(422, "selected device differs from the frozen camera profile")
    source_kind = "simulation" if development else body.get("source_kind", "unknown")
    if not isinstance(source_kind, str):
        raise HTTPException(422, "source_kind must be text")
    metadata = capture_metadata(
        source_kind, body.get("capture_attested_by", ""), body.get("session_name", "")
    )
    attester = metadata["capture_attested_by"]
    with _state["lock"]:
        engine = get_engine()
        if engine is not None and engine.mode != "preview":
            raise HTTPException(409, "a live capture already owns the source")
        if engine is not None and (
            development != _state["synthetic"] or device_index != _state["device"]
        ):
            raise HTTPException(409, "close preview explicitly before selecting another source")
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
            **metadata,
            "created_at": utcnow(),
            "recording_state": "running",
            "profile": profile,
            "profile_id": body["profile_id"],
            "profile_revision": profile["revision"],
            "notes": str(body.get("notes", "")),
            "qualification": "unqualified",
        }
        atomic_json(session_dir / "session.json", session)
        try:
            return _start_engine(
                device_index, development, profile, sid, session_dir, source_kind, attester
            )
        except HTTPException:
            session["recording_state"] = "failed"
            atomic_json(session_dir / "session.json", session)
            raise
        except ValueError as exc:
            session.update(recording_state="failed", reason=str(exc))
            atomic_json(session_dir / "session.json", session)
            raise HTTPException(422, str(exc)) from exc
        except (RuntimeError, OSError) as exc:
            session.update(recording_state="failed", reason=str(exc))
            atomic_json(session_dir / "session.json", session)
            raise HTTPException(503, str(exc)) from exc


@router.post("/stop")
def live_stop() -> dict:
    return _stop_engine()


@router.get("/status")
def live_status() -> dict:
    engine = get_engine()
    if engine is None:
        return {
            "running": False,
            "mode": "closed",
            "camera_open": False,
            "session_id": _state.get("session_id"),
            "recording_state": "stopped",
            "readiness": {
                "camera_connected": False,
                "can_record": False,
                "issues": [],
                "physically_qualified": False,
            },
        }
    snapshot = engine.snapshot()
    latencies = [event.latency_ms for event in _state["bus"].recent(100)]
    return {
        **snapshot,
        "events": [event.to_dict() for event in _state["bus"].recent(20)],
        "session_id": engine.session_id or None,
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
