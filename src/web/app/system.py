"""System health information for the dashboard (no SDR hardware required)."""

import os
import socket
import time

import psutil


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
        "load_avg": load_avg,
        "memory": {"used": vm.used, "total": vm.total, "percent": vm.percent},
        "disk": {"used": disk.used, "total": disk.total, "percent": disk.percent},
        "uptime_seconds": int(time.time() - psutil.boot_time()),
    }
