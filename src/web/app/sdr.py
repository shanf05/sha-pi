"""RTL-SDR access and the single-receiver "mode" manager.

The dongle is a single-channel, OS-exclusive device: only one process can open it
and it can only be tuned to one frequency at a time. The web interface therefore
treats SDR features as mutually exclusive *modes* (only one active at once), owned
by a single SdrController.

Modes stream live frames to the browser over a broadcast callback. When no dongle is
present, modes fall back to a clearly-labelled SIMULATED data source so the UI and
streaming pipeline are fully usable/testable before the hardware arrives; they switch
to real RF automatically once a dongle is detected.

This module imports no web framework so the parsing logic stays unit-testable in
isolation (`python3 app/sdr.py --selftest`).
"""

import asyncio
import math
import os
import random
import re
import subprocess
import time

RTL_TEST = "rtl_test"
RTL_POWER = "rtl_power"

# Frequency range scanned by the spectrum mode: "start:stop:step" (default FM band).
SPECTRUM_RANGE = os.environ.get("SHAPI_SPECTRUM_RANGE", "88M:108M:50k")


# --------------------------------------------------------------------------- #
# Device status probe
# --------------------------------------------------------------------------- #
def get_status():
    """Probe for an attached RTL-SDR using rtl_test.

    Only meaningful while no streaming mode holds the device. Returns a dict
    describing whether a dongle is present and what it is.
    """
    out = ""
    try:
        proc = subprocess.run(
            [RTL_TEST, "-t"], capture_output=True, text=True, timeout=6
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
    return {"available": available, "device": device, "tuner": tuner, "raw": out.strip()}


# --------------------------------------------------------------------------- #
# Spectrum helpers (pure / testable)
# --------------------------------------------------------------------------- #
def _to_hz(token):
    """Parse an rtl_power-style frequency token like '88M', '50k', '433920000'."""
    token = token.strip()
    mult = 1
    if token and token[-1] in "kKmMgG":
        mult = {"k": 1e3, "m": 1e6, "g": 1e9}[token[-1].lower()]
        token = token[:-1]
    return int(float(token) * mult)


def parse_range(spec):
    """'start:stop:step' -> (start_hz, stop_hz, step_hz, n_bins)."""
    start_s, stop_s, step_s = spec.split(":")
    start, stop, step = _to_hz(start_s), _to_hz(stop_s), _to_hz(step_s)
    n_bins = max(1, round((stop - start) / step))
    return start, stop, step, n_bins


class SpectrumAssembler:
    """Reassembles rtl_power CSV segment lines into one frame per full sweep.

    rtl_power prints one CSV line per frequency segment; a complete sweep is the
    set of segments covering the whole range, repeated every interval. A new sweep
    is detected when the lowest start frequency reappears.
    """

    def __init__(self):
        self._segments = {}
        self._first_low = None

    def feed(self, line):
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            return None
        try:
            low = int(float(parts[2]))
            high = int(float(parts[3]))
            dbs = [float(x) for x in parts[6:] if x != ""]
        except ValueError:
            return None

        frame = None
        if self._first_low is None:
            self._first_low = low
        elif low == self._first_low and self._segments:
            frame = self._flush()
        self._segments[low] = (high, dbs)
        return frame

    def _flush(self):
        ordered = sorted(self._segments.items())
        bins = []
        for _low, (_high, dbs) in ordered:
            bins.extend(dbs)
        f_start = ordered[0][0]
        f_stop = ordered[-1][1][0]
        self._segments = {}
        return {"f_start": f_start, "f_stop": f_stop, "bins": bins}


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #
class SdrController:
    """Owns the dongle and runs at most one streaming mode at a time."""

    MODES = ("spectrum",)

    def __init__(self, broadcast):
        # broadcast: async callable taking a JSON-serialisable dict.
        self._broadcast = broadcast
        self._lock = asyncio.Lock()
        self._task = None
        self._proc = None
        self.mode = None
        self.simulated = False
        self.latest_frame = None
        f_start, f_stop, _step, n_bins = parse_range(SPECTRUM_RANGE)
        self._meta = {"f_start": f_start, "f_stop": f_stop, "n_bins": n_bins}

    def state(self):
        return {
            "type": "mode",
            "mode": self.mode,
            "simulated": self.simulated,
            "meta": self._meta,
        }

    async def set_mode(self, name):
        if name in ("off", "", None):
            name = None
        elif name not in self.MODES:
            raise ValueError(f"unknown mode: {name}")

        async with self._lock:
            if name == self.mode:
                return self.state()
            await self._stop_locked()
            self.mode = name
            self.latest_frame = None
            if name == "spectrum":
                status = await asyncio.to_thread(get_status)
                self.simulated = not status.get("available", False)
                runner = self._run_spectrum_real if not self.simulated else self._run_spectrum_sim
                self._task = asyncio.create_task(self._guard(runner()))
            await self._broadcast(self.state())
            return self.state()

    async def _stop_locked(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._kill_proc()
        self.simulated = False

    async def _kill_proc(self):
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
        self._proc = None

    async def _guard(self, coro):
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # keep the server alive if a decoder dies
            await self._broadcast({"type": "error", "mode": self.mode, "message": str(exc)})

    async def _emit(self, frame):
        msg = {"type": "frame", "mode": "spectrum", "simulated": self.simulated, "ts": time.time()}
        msg.update(frame)
        self.latest_frame = msg
        await self._broadcast(msg)

    async def _run_spectrum_real(self):
        self._proc = await asyncio.create_subprocess_exec(
            RTL_POWER, "-f", SPECTRUM_RANGE, "-i", "1", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        assembler = SpectrumAssembler()
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            frame = assembler.feed(line.decode(errors="replace"))
            if frame:
                await self._emit(frame)

    async def _run_spectrum_sim(self):
        """Synthetic spectrum: noise floor plus a few drifting peaks. Clearly flagged."""
        f_start, f_stop = self._meta["f_start"], self._meta["f_stop"]
        n = self._meta["n_bins"]
        peaks = [{"pos": random.uniform(0.1, 0.9), "w": random.uniform(0.01, 0.03),
                  "amp": random.uniform(15, 35), "drift": random.uniform(-0.004, 0.004)}
                 for _ in range(4)]
        while True:
            bins = []
            for i in range(n):
                x = i / n
                val = -30.0 + random.uniform(-2.5, 2.5)
                for pk in peaks:
                    val += pk["amp"] * math.exp(-((x - pk["pos"]) ** 2) / (2 * pk["w"] ** 2))
                bins.append(round(val, 1))
            for pk in peaks:
                pk["pos"] += pk["drift"]
                if not 0.05 < pk["pos"] < 0.95:
                    pk["drift"] *= -1
            await self._emit({"f_start": f_start, "f_stop": f_stop, "bins": bins})
            await asyncio.sleep(0.5)


# --------------------------------------------------------------------------- #
# Self-test for the pure parsing logic (no hardware, no web framework)
# --------------------------------------------------------------------------- #
def _selftest():
    assert _to_hz("88M") == 88_000_000
    assert _to_hz("50k") == 50_000
    assert parse_range("88M:108M:50k") == (88_000_000, 108_000_000, 50_000, 400)

    asm = SpectrumAssembler()
    # sweep 1, two segments
    assert asm.feed("2026-01-01, 00:00:00, 88000000, 90000000, 1000000, 10, -30, -29") is None
    assert asm.feed("2026-01-01, 00:00:01, 90000000, 92000000, 1000000, 10, -28, -27") is None
    # sweep 2 begins -> previous sweep flushes
    frame = asm.feed("2026-01-01, 00:00:02, 88000000, 90000000, 1000000, 10, -31, -30")
    assert frame == {"f_start": 88000000, "f_stop": 92000000,
                     "bins": [-30, -29, -28, -27]}, frame
    print("sdr selftest: OK")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
