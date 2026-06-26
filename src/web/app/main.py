"""sha-pi station web interface.

A single dashboard that grows by tabs: system health now, SDR decoder modes and
the weather station later. Served on the LAN (see scripts/install-web.sh).
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import sdr, system

app = FastAPI(title="sha-pi station")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/api/system")
def api_system():
    return system.get_system_info()


@app.get("/api/sdr/status")
def api_sdr_status():
    return sdr.get_status()


# Static dashboard last, mounted at root so "/" serves index.html.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
