#!/usr/bin/env bash
#
# Install build dependencies for the station's driver stack and remove distro
# packages that conflict with the in-tree rtl-sdr-blog driver.
#
# The RTL-SDR Blog V4 requires the rtl-sdr-blog fork (vendored under
# src/drivers/rtl-sdr-blog). The distribution's rtl-sdr / librtlsdr-dev packages
# ship an older libusb-based driver that does not support the V4 and will shadow
# our build, so they must be purged first.
#
# Idempotent: safe to re-run.

set -euo pipefail

if [[ $EUID -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

echo "==> Updating apt package index"
$SUDO apt-get update

echo "==> Installing build dependencies"
$SUDO apt-get install -y \
    build-essential \
    cmake \
    pkg-config \
    git \
    libusb-1.0-0-dev

echo "==> Removing conflicting distro RTL-SDR packages (if present)"
$SUDO apt-get purge -y '^librtlsdr.*' rtl-sdr 2>/dev/null || true
$SUDO apt-get autoremove -y || true

echo "==> Dependencies ready"
