#!/usr/bin/env bash
#
# Build the station's driver stack from source and install into the system
# standard directories (/usr/local/{bin,lib,include}).
#
# This is the single entrypoint for "build the whole stack". Today it builds the
# rtl-sdr-blog driver; add further components below as they are introduced.
#
# Run scripts/install-deps.sh first. Idempotent: safe to re-run.

set -euo pipefail

# Resolve repo root from this script's location (no hardcoded paths).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$REPO_ROOT/build"

if [[ $EUID -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

build_rtl_sdr_blog() {
    local src="$REPO_ROOT/src/drivers/rtl-sdr-blog"
    local out="$BUILD_DIR/rtl-sdr-blog"

    echo "==> [rtl-sdr-blog] Configuring (out-of-source: $out)"
    cmake -S "$src" -B "$out" \
        -DCMAKE_BUILD_TYPE=Release \
        -DINSTALL_UDEV_RULES=ON \
        -DDETACH_KERNEL_DRIVER=ON

    echo "==> [rtl-sdr-blog] Building"
    cmake --build "$out" -j"$(nproc)"

    echo "==> [rtl-sdr-blog] Installing to /usr/local"
    $SUDO cmake --install "$out"
    $SUDO ldconfig

    echo "==> [rtl-sdr-blog] Reloading udev rules"
    $SUDO udevadm control --reload-rules
    $SUDO udevadm trigger

    echo "==> [rtl-sdr-blog] Blacklisting in-kernel DVB driver (dvb_usb_rtl28xxu)"
    echo "blacklist dvb_usb_rtl28xxu" | $SUDO tee /etc/modprobe.d/blacklist-rtl-sdr.conf >/dev/null
    $SUDO modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
}

echo "==> Building station driver stack"
build_rtl_sdr_blog

cat <<'EOF'

==> Done.

Verify the RTL-SDR Blog V4:
  1. (Re)plug the dongle, or reboot if the DVB module was loaded before this run.
  2. rtl_test          # expect: "RTL-SDR Blog V4 Detected"
  3. rtl_eeprom        # reads device EEPROM / info
EOF
