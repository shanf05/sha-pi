"""System health information for the dashboard (no SDR hardware required)."""

import os
import re
import socket
import subprocess
import time

import psutil

# rtl_power-style line: "<RAIL>_A current(0)=0.096A" / "<RAIL>_V volt(8)=3.70V"
_PMIC_LINE = re.compile(r"(\S+?)_(A|V)\s+\w+\(\d+\)=([0-9.]+)")


def _power_watts():
    """Total board power on a Pi 5, summed from the PMIC rails (volts x amps).

    Returns watts, or None if vcgencmd/PMIC is unavailable (e.g. non-Pi-5).
    """
    try:
        out = subprocess.run(
            ["vcgencmd", "pmic_read_adc"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None

    rails = {}
    for base, kind, val in _PMIC_LINE.findall(out):
        rails.setdefault(base, {})[kind] = float(val)
    total = 0.0
    found = False
    for r in rails.values():
        if "A" in r and "V" in r:
            total += r["A"] * r["V"]
            found = True
    return round(total, 2) if found else None


def _cpu_temp_c():
    """CPU temperature in degrees Celsius, or None if unavailable."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as fh:
            return round(int(fh.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def _ipv4_addresses():
    """Non-loopback IPv4 addresses, so the dashboard can show how to reach the Pi."""
    addresses = []
    for name, snics in psutil.net_if_addrs().items():
        if name == "lo":
            continue
        for snic in snics:
            if snic.family == socket.AF_INET:
                addresses.append({"interface": name, "address": snic.address})
    return addresses


def get_system_info():
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uname = os.uname()
    try:
        load_avg = list(os.getloadavg())
    except OSError:
        load_avg = [None, None, None]

    return {
        "hostname": socket.gethostname(),
        "addresses": _ipv4_addresses(),
        "os": f"{uname.sysname} {uname.release}",
        "machine": uname.machine,
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "cpu_temp_c": _cpu_temp_c(),
        "power_watts": _power_watts(),
        "load_avg": load_avg,
        "memory": {"used": vm.used, "total": vm.total, "percent": vm.percent},
        "disk": {"used": disk.used, "total": disk.total, "percent": disk.percent},
        "uptime_seconds": int(time.time() - psutil.boot_time()),
    }
