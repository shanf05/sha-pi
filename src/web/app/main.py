"""sha-pi station web interface.

A single dashboard that grows by tabs: system health and SDR decoder modes now,
the weather station later. Served on the LAN (see scripts/install-web.sh).

SDR data is streamed live to browsers over a WebSocket. Because the receiver is a
single exclusive device, the active SDR mode is global: switching mode affects every
connected client.
"""

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import sdr, system

app = FastAPI(title="sha-pi station")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Connected dashboard WebSockets. The active SDR mode broadcasts frames to all of them.
_clients: set[WebSocket] = set()


async def broadcast(message: dict):
    dead = []
    for ws in list(_clients):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


controller = sdr.SdrController(broadcast)


@app.get("/api/system")
def api_system():
    return system.get_system_info()


@app.get("/api/sdr/status")
async def api_sdr_status():
    # The device is exclusive: don't probe with rtl_test while a mode holds it.
    if controller.mode is not None:
        return {
            "available": True,
            "in_use_by": controller.mode,
            "simulated": controller.simulated,
            "raw": f"device in use by '{controller.mode}' mode"
            + (" (simulated)" if controller.simulated else ""),
        }
    return await asyncio.to_thread(sdr.get_status)


@app.post("/api/sdr/mode")
async def api_sdr_mode(payload: dict):
    try:
        state = await controller.set_mode(payload.get("mode"))
    except ValueError as exc:
        return {"error": str(exc)}
    return state


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    try:
        await websocket.send_json(controller.state())
        if controller.latest_frame:
            await websocket.send_json(controller.latest_frame)
        while True:
            # We don't expect client messages; this keeps the socket open and
            # detects disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)


# Static dashboard last, mounted at root so "/" serves index.html.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
