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
import json
import math
import os
import random
import re
import subprocess
import time

RTL_TEST = "rtl_test"
RTL_POWER = "rtl_power"
RTL_433 = "rtl_433"

# Frequency range scanned by the spectrum mode: "start:stop:step" (default FM band).
SPECTRUM_RANGE = os.environ.get("SHAPI_SPECTRUM_RANGE", "88M:108M:50k")

# Frequency rtl_433 listens on for the 433 MHz sensor mode (433.92 MHz ISM by default;
# set to 868M for European 868 MHz devices).
RTL433_FREQ = os.environ.get("SHAPI_RTL433_FREQ", "433.92M")

# Receiver location, used to centre the ADS-B map and (later) compute range.
# Defaults to a central spot for the simulation; set to your real location.
RX_LAT = float(os.environ.get("SHAPI_RX_LAT", "50.05"))
RX_LON = float(os.environ.get("SHAPI_RX_LON", "8.60"))


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
# 433 MHz sensor decoding (pure / testable)
# --------------------------------------------------------------------------- #
# rtl_433 JSON keys that identify/annotate a record rather than being a reading.
# Everything else in a record is treated as a live measurement field.
_RTL433_META = {
    "time", "model", "id", "channel", "subtype", "type", "mic", "mod",
    "freq", "freq1", "freq2", "rssi", "snr", "noise", "protocol",
    "sequence_num", "battery_ok",
}


class Rtl433Aggregator:
    """Folds the stream of rtl_433 JSON events into a live per-device table.

    rtl_433 emits one JSON object per decoded packet, and a given sensor
    re-transmits every few seconds. We key on model/id/channel and keep, per
    device, the latest measurement fields, a last-seen time and an event count,
    so the UI shows one row per physical sensor instead of a raw event log.
    """

    def __init__(self):
        self._sensors = {}

    @staticmethod
    def key_of(rec):
        parts = [str(rec.get("model", "unknown"))]
        if rec.get("id") is not None:
            parts.append("id" + str(rec["id"]))
        if rec.get("channel") is not None:
            parts.append("ch" + str(rec["channel"]))
        return "/".join(parts)

    def feed(self, rec, ts):
        """Fold one decoded record (a dict) seen at time `ts`; returns its entry."""
        key = self.key_of(rec)
        fields = {k: v for k, v in rec.items() if k not in _RTL433_META}
        entry = self._sensors.get(key)
        if entry is None:
            entry = {
                "key": key, "model": rec.get("model"),
                "id": rec.get("id"), "channel": rec.get("channel"),
                "first_seen": ts, "count": 0,
            }
            self._sensors[key] = entry
        entry["fields"] = fields
        if "battery_ok" in rec:
            entry["battery_ok"] = rec["battery_ok"]
        entry["last_seen"] = ts
        entry["count"] += 1
        return entry

    def snapshot(self):
        return sorted(self._sensors.values(), key=lambda e: e["key"])


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #
class SdrController:
    """Owns the dongle and runs at most one streaming mode at a time."""

    MODES = ("spectrum", "adsb", "sensors")

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
        self._meta = {
            "f_start": f_start, "f_stop": f_stop, "n_bins": n_bins,
            "sensors_freq": _to_hz(RTL433_FREQ),
        }
        self._rx = {"lat": RX_LAT, "lon": RX_LON}

    def state(self):
        return {
            "type": "mode",
            "mode": self.mode,
            "simulated": self.simulated,
            "meta": self._meta,
            "rx": self._rx,
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
            elif name == "adsb":
                # Real path (dump1090) is added in the ADS-B hardware phase; until
                # then ADS-B always runs simulated.
                self.simulated = True
                self._task = asyncio.create_task(self._guard(self._run_adsb_sim()))
            elif name == "sensors":
                status = await asyncio.to_thread(get_status)
                self.simulated = not status.get("available", False)
                runner = self._run_sensors_real if not self.simulated else self._run_sensors_sim
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
        msg = {"type": "frame", "mode": self.mode, "simulated": self.simulated, "ts": time.time()}
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

    async def _run_adsb_sim(self):
        """Synthetic aircraft drifting around the receiver. Clearly flagged simulated.

        Mirrors the fields dump1090 exposes (hex, flight, lat, lon, alt, gs, track)
        so the real path can drop in unchanged later.
        """
        lat0, lon0 = self._rx["lat"], self._rx["lon"]
        airlines = ["DLH", "BAW", "RYR", "EZY", "AFR", "KLM", "SWR", "WZZ"]
        planes = []
        for _ in range(7):
            planes.append({
                "hex": "%06x" % random.randint(0, 0xFFFFFF),
                "flight": random.choice(airlines) + str(random.randint(100, 999)),
                "lat": lat0 + random.uniform(-1.3, 1.3),
                "lon": lon0 + random.uniform(-1.9, 1.9),
                "alt": random.randint(3000, 39000),
                "gs": random.randint(280, 500),
                "track": random.uniform(0, 360),
            })
        while True:
            for p in planes:
                # gs is knots (nm/h); convert to degrees travelled this second.
                step_nm = p["gs"] / 3600.0
                p["lat"] += step_nm / 60.0 * math.cos(math.radians(p["track"]))
                p["lon"] += (step_nm / 60.0 * math.sin(math.radians(p["track"]))
                             / max(0.1, math.cos(math.radians(p["lat"]))))
                p["track"] = (p["track"] + random.uniform(-2, 2)) % 360
                # turn back toward the centre if it drifts too far away
                if abs(p["lat"] - lat0) > 2.2 or abs(p["lon"] - lon0) > 3.2:
                    p["track"] = (p["track"] + 180) % 360
            aircraft = [{
                "hex": p["hex"], "flight": p["flight"],
                "lat": round(p["lat"], 4), "lon": round(p["lon"], 4),
                "alt": p["alt"], "gs": p["gs"], "track": round(p["track"]),
            } for p in planes]
            await self._emit({"aircraft": aircraft, "stats": {"count": len(aircraft)}})
            await asyncio.sleep(1.0)

    async def _run_sensors_real(self):
        """Decode 433/868 MHz sensors with rtl_433's line-buffered JSON output.

        rtl_433 prints one JSON object per decoded packet on stdout (status text
        goes to stderr, which we drop). Each line is folded into the per-device
        table and the whole snapshot is streamed to the browser.
        """
        self._proc = await asyncio.create_subprocess_exec(
            RTL_433, "-f", RTL433_FREQ, "-F", "json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        agg = Rtl433Aggregator()
        events = 0
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if not text.startswith("{"):
                continue
            try:
                rec = json.loads(text)
            except ValueError:
                continue
            agg.feed(rec, time.time())
            events += 1
            snap = agg.snapshot()
            await self._emit({"sensors": snap, "stats": {"count": len(snap), "events": events}})

    async def _run_sensors_sim(self):
        """Synthetic 433 MHz sensor traffic. Clearly flagged simulated.

        Emits rtl_433-shaped records (model/id/channel + measurement fields) for a
        small stable fleet whose values random-walk, so the real `rtl_433 -F json`
        path drops in unchanged. Each device re-transmits at a random interval,
        just like real ISM-band sensors.
        """
        agg = Rtl433Aggregator()
        events = 0
        # (base record, {field: (max step, lo, hi)} for the random walk)
        devices = [
            ({"model": "Acurite-Tower", "id": 4231, "channel": 1,
              "temperature_C": 21.5, "humidity": 47, "battery_ok": 1},
             {"temperature_C": (0.2, 18, 26), "humidity": (1, 35, 65)}),
            ({"model": "Nexus-TH", "id": 113, "channel": 2,
              "temperature_C": 5.4, "humidity": 82, "battery_ok": 1},
             {"temperature_C": (0.3, -2, 12), "humidity": (1.5, 60, 95)}),
            ({"model": "Bresser-5in1", "id": 88,
              "temperature_C": 19.8, "humidity": 53, "wind_avg_km_h": 12.0,
              "wind_max_km_h": 21.0, "wind_dir_deg": 210, "rain_mm": 4.2,
              "battery_ok": 1},
             {"temperature_C": (0.2, 12, 28), "humidity": (1, 40, 70),
              "wind_avg_km_h": (1.5, 0, 40), "wind_max_km_h": (2, 0, 60),
              "wind_dir_deg": (8, 0, 359)}),
            ({"model": "LaCrosse-TX29", "id": 27,
              "temperature_C": 22.1, "battery_ok": 0},  # low-battery example
             {"temperature_C": (0.15, 18, 25)}),
            ({"model": "Prologue-TH", "id": 201, "channel": 3,
              "temperature_C": -3.2, "humidity": 90, "battery_ok": 1},
             {"temperature_C": (0.25, -8, 4), "humidity": (1, 70, 99)}),
        ]
        while True:
            base, walk = random.choice(devices)
            for field, (step, lo, hi) in walk.items():
                val = max(lo, min(hi, base[field] + random.uniform(-step, step)))
                base[field] = round(val, 1) if isinstance(base[field], float) else round(val)
            # Rain only ever accumulates.
            if "rain_mm" in base:
                base["rain_mm"] = round(base["rain_mm"] + random.uniform(0, 0.2), 1)
            agg.feed(dict(base), time.time())
            events += 1
            snap = agg.snapshot()
            await self._emit({"sensors": snap, "stats": {"count": len(snap), "events": events}})
            await asyncio.sleep(random.uniform(0.6, 1.8))


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

    agg = Rtl433Aggregator()
    e1 = agg.feed({"model": "Nexus-TH", "id": 113, "channel": 2,
                   "temperature_C": 5.4, "humidity": 82, "battery_ok": 1}, 100.0)
    assert e1["key"] == "Nexus-TH/id113/ch2", e1["key"]
    assert e1["fields"] == {"temperature_C": 5.4, "humidity": 82}, e1["fields"]
    assert e1["battery_ok"] == 1 and e1["count"] == 1
    # a retransmit of the same device updates in place (not a new row)
    agg.feed({"model": "Nexus-TH", "id": 113, "channel": 2,
              "temperature_C": 5.6, "humidity": 81, "battery_ok": 1}, 130.0)
    # a different device is a separate row
    agg.feed({"model": "Acurite-Tower", "id": 4231, "channel": 1,
              "temperature_C": 21.5, "battery_ok": 1}, 131.0)
    snap = agg.snapshot()
    assert len(snap) == 2, snap
    nexus = next(s for s in snap if s["key"] == "Nexus-TH/id113/ch2")
    assert nexus["count"] == 2 and nexus["fields"]["temperature_C"] == 5.6
    assert nexus["last_seen"] == 130.0 and nexus["first_seen"] == 100.0
    print("sdr selftest: OK")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
