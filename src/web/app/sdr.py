"""RTL-SDR access.

The dongle is a single-channel, OS-exclusive device: only one process can open it
and it can only be tuned to one frequency at a time. The web interface therefore
treats SDR features as mutually exclusive "modes" (only one active at once).

Phase 1 implements the device-status probe. Streaming decoder modes (spectrum,
ADS-B, rtl_433) are added in later phases, each tested against real hardware.
"""

import re
import subprocess

RTL_TEST = "rtl_test"


def get_status():
    """Probe for an attached RTL-SDR using rtl_test.

    Only meaningful while no streaming decoder mode holds the device. Returns a
    dict describing whether a dongle is present and what it is.
    """
    out = ""
    try:
        proc = subprocess.run(
            [RTL_TEST, "-t"],
            capture_output=True,
            text=True,
            timeout=6,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return {"available": False, "error": "rtl_test not found (driver not installed)"}
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(out, bytes):
            out = out.decode(errors="replace")

    available = "Found" in out and "No supported devices found" not in out

    tuner = None
    match = re.search(r"Found (.+? tuner)", out)
    if match:
        tuner = match.group(1).strip()

    device = None
    match = re.search(r"^\s*0:\s*(.+)$", out, re.MULTILINE)
    if match:
        device = match.group(1).strip()

    return {
        "available": available,
        "device": device,
        "tuner": tuner,
        "raw": out.strip(),
    }
