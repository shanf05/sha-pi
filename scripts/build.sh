#!/usr/bin/env bash
#
# Build the station's SDR stack from source and install into the system standard
# directories (/usr/local/{bin,lib,include}).
#
# Third-party sources are consumed unmodified: each is cloned at a pinned commit
# (see scripts/sources.env) into build/ (gitignored), built out-of-source, and
# installed. This is the single entrypoint for "build the whole stack".
#
# Run scripts/install-deps.sh first. Idempotent: safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$REPO_ROOT/build"
SRC_DIR="$BUILD_DIR/sources"

# shellcheck source=sources.env
source "$SCRIPT_DIR/sources.env"

if [[ $EUID -eq 0 ]]; then SUDO=""; else SUDO="sudo"; fi

# Configure an out-of-source cmake build, discarding a stale cache if it was
# generated for a different source path (e.g. after a source location change).
cmake_configure() {
    local src="$1" out="$2"; shift 2
    if [[ -f "$out/CMakeCache.txt" ]] \
       && ! grep -qx "CMAKE_HOME_DIRECTORY:INTERNAL=$src" "$out/CMakeCache.txt"; then
        echo "    (clearing stale build cache in $out)"
        rm -rf "$out"
    fi
    cmake -S "$src" -B "$out" "$@"
}

# Clone (or update) $repo into $dest and check out the pinned $commit.
fetch_source() {
    local repo="$1" commit="$2" dest="$3"
    if [[ ! -d "$dest/.git" ]]; then
        rm -rf "$dest"
        git clone "$repo" "$dest"
    fi
    git -C "$dest" fetch --quiet origin
    git -C "$dest" checkout --quiet "$commit"
    echo "    $dest @ $(git -C "$dest" rev-parse --short HEAD)"
}

build_rtl_sdr_blog() {
    local src="$SRC_DIR/rtl-sdr-blog"
    local out="$BUILD_DIR/rtl-sdr-blog"
    echo "==> [rtl-sdr-blog] Fetching $RTL_SDR_BLOG_COMMIT"
    fetch_source "$RTL_SDR_BLOG_REPO" "$RTL_SDR_BLOG_COMMIT" "$src"

    echo "==> [rtl-sdr-blog] Building"
    cmake_configure "$src" "$out" \
        -DCMAKE_BUILD_TYPE=Release \
        -DINSTALL_UDEV_RULES=ON \
        -DDETACH_KERNEL_DRIVER=ON
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

build_rtl_433() {
    local src="$SRC_DIR/rtl_433"
    local out="$BUILD_DIR/rtl_433"
    echo "==> [rtl_433] Fetching $RTL_433_COMMIT"
    fetch_source "$RTL_433_REPO" "$RTL_433_COMMIT" "$src"

    echo "==> [rtl_433] Building (against /usr/local librtlsdr)"
    # Find our rtl-sdr-blog librtlsdr (installed above) rather than any distro copy.
    PKG_CONFIG_PATH="/usr/local/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" \
        cmake_configure "$src" "$out" \
            -DCMAKE_BUILD_TYPE=Release \
            -DCMAKE_PREFIX_PATH=/usr/local
    cmake --build "$out" -j"$(nproc)"

    echo "==> [rtl_433] Installing to /usr/local"
    $SUDO cmake --install "$out"
    $SUDO ldconfig
}

echo "==> Building station SDR stack"
build_rtl_sdr_blog
build_rtl_433

cat <<'EOF'

==> Done.

Verify:
  rtl_test            # RTL-SDR driver (expect "RTL-SDR Blog V4 Detected" when the dongle is in)
  rtl_433 -V          # 433/868 MHz decoder version
EOF
