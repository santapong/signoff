"""Live monitoring endpoints (Phase 5): WS event stream + dashboard HTML."""
from __future__ import annotations

import json
import threading

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from services.live_worker.engine import (
    EventBus, LATENCY_BUDGET_MS, LiveEngine, make_source)

router = APIRouter(prefix="/live", tags=["live"])

_state: dict = {"engine": None, "lock": threading.Lock(), "device": None}


def get_engine() -> LiveEngine | None:
    return _state["engine"]


def _start_engine(device_index: int | None, synthetic: bool) -> dict:
    with _state["lock"]:
        if _state["engine"] is not None:
            return {"status": "already_running"}
        bus = EventBus()
        src = make_source(device_index if not synthetic else None,
                          synthetic=synthetic)
        engine = LiveEngine(src, bus)
        engine.start()
        _state["engine"] = engine
        _state["bus"] = bus
        _state["device"] = device_index
        return {"status": "started",
                "synthetic": synthetic,
                "latency_budget_ms": LATENCY_BUDGET_MS}


def _stop_engine() -> dict:
    with _state["lock"]:
        eng = _state["engine"]
        if eng is None:
            return {"status": "not_running"}
        eng.stop()
        eng.source.close()
        _state["engine"] = None
        return {"status": "stopped"}


@router.post("/start")
def live_start(synthetic: bool = True, device: int | None = None) -> dict:
    return _start_engine(device, synthetic)


@router.post("/stop")
def live_stop() -> dict:
    return _stop_engine()


@router.get("/status")
def live_status() -> dict:
    eng = _state["engine"]
    if eng is None:
        return {"running": False}
    recent_events = [e.to_dict() for e in _state["bus"].recent(20)]
    latencies = [e.latency_ms for e in _state["bus"].recent(100)]
    return {
        "running": True,
        "synthetic_device": _state["device"] is None,
        "completed_cycles": eng.completed_cycles,
        "camera_down": eng.camera_down,
        "max_latency_ms": round(max(latencies), 1) if latencies else 0.0,
        "latency_budget_ms": LATENCY_BUDGET_MS,
        "events": recent_events,
    }


@router.websocket("/ws")
async def live_ws(ws: WebSocket):
    await ws.accept()
    import asyncio
    queue: list = []

    def on_event(ev):
        queue.append(ev.to_dict())

    bus = _state.get("bus")
    if bus is None:
        await ws.send_json({"type": "error",
                            "payload": {"message": "engine not running"}})
        await ws.close()
        return
    bus.subscribe(on_event)
    try:
        while True:
            if queue:
                await ws.send_json(queue.pop(0))
            else:
                await ws.send_json({"type": "heartbeat",
                                    "capture_ms": 0.0, "emit_ms": 0.0,
                                    "latency_ms": 0.0,
                                    "within_budget": True})
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=1.0)
                if msg == "stop":
                    break
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        pass
    finally:
        try:
            bus._subscribers.remove(on_event)
        except (ValueError, AttributeError):
            pass


DASHBOARD_HTML = """<!DOCTYPE html>
<html><head><title>Signoff Live</title>
<style>
 body{font-family:system-ui;background:#111;color:#eee;margin:2rem}
 .card{background:#1c1c1c;border-radius:8px;padding:1rem;margin-bottom:1rem}
 .ok{color:#6f6} .bad{color:#f66}
 li{margin:.2rem 0;font-family:monospace}
</style></head><body>
<h1>Signoff — Live Monitoring</h1>
<div class="card">
 <div>Cycles completed: <b id="cycles">-</b></div>
 <div>Camera: <b id="cam">-</b></div>
 <div>Max latency: <b id="lat">-</b> ms (budget <span id="budget">-</span> ms)</div>
</div>
<div class="card"><h3>Events</h3><ul id="log"></ul></div>
<script>
const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
const ws = new WebSocket(`${wsProto}://${location.host}/live/ws`);
const log = document.getElementById('log');
ws.onmessage = (m) => {
  const ev = JSON.parse(m.data);
  if (ev.type === 'heartbeat') { refresh(); return; }
  const li = document.createElement('li');
  li.textContent = `${ev.type} latency=${ev.latency_ms}ms ${ev.within_budget?'OK':'OVER BUDGET'}`;
  li.className = ev.within_budget ? 'ok' : 'bad';
  log.prepend(li);
  while (log.children.length > 30) log.removeChild(log.lastChild);
  refresh();
};
async function refresh(){
  const r = await fetch('/live/status'); const s = await r.json();
  if (!s.running) return;
  document.getElementById('cycles').textContent = s.completed_cycles;
  document.getElementById('cam').textContent = s.camera_down ? 'LOST' : 'up';
  document.getElementById('lat').textContent = s.max_latency_ms;
  document.getElementById('budget').textContent = s.latency_budget_ms;
}
refresh(); setInterval(refresh, 3000);
</script></body></html>"""


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)
