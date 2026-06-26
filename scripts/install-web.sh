#!/usr/bin/env bash
#
# Install/refresh the sha-pi station web interface and its boot autostart.
#
# Creates a Python venv (outside the repo, so rsync/git never touch it),
# installs the app dependencies, and writes + enables a systemd service that
# serves the dashboard on port 80 so the Pi is reachable at http://<pi-ip>/
# right after power-on. Idempotent: safe to re-run after code changes
# (re-run, then `sudo systemctl restart sha-pi-web`).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WEB_DIR="$REPO_ROOT/src/web"
VENV="$HOME/.venvs/sha-pi-web"
SERVICE="/etc/systemd/system/sha-pi-web.service"
RUN_USER="$(id -un)"

# Configurable runtime settings, baked into the systemd unit. Override per run, e.g.
#   SHAPI_RX_LAT=48.137 SHAPI_RX_LON=11.575 bash scripts/install-web.sh
RX_LAT="${SHAPI_RX_LAT:-50.05}"          # ADS-B map: receiver latitude
RX_LON="${SHAPI_RX_LON:-8.60}"           # ADS-B map: receiver longitude
SPECTRUM_RANGE="${SHAPI_SPECTRUM_RANGE:-88M:108M:50k}"  # spectrum sweep start:stop:step
RTL433_FREQ="${SHAPI_RTL433_FREQ:-433.92M}"  # 433 MHz sensor mode listen frequency

if [[ $EUID -eq 0 ]]; then SUDO=""; else SUDO="sudo"; fi

echo "==> Installing Python venv tooling"
$SUDO apt-get update
$SUDO apt-get install -y python3-venv python3-pip

echo "==> Creating virtualenv at $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$WEB_DIR/requirements.txt"

echo "==> Writing systemd unit $SERVICE"
$SUDO tee "$SERVICE" >/dev/null <<EOF
[Unit]
Description=sha-pi station web interface
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$WEB_DIR
ExecStart=$VENV/bin/uvicorn app.main:app --host 0.0.0.0 --port 80
Environment=SHAPI_RX_LAT=$RX_LAT
Environment=SHAPI_RX_LON=$RX_LON
Environment=SHAPI_SPECTRUM_RANGE=$SPECTRUM_RANGE
Environment=SHAPI_RTL433_FREQ=$RTL433_FREQ
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo "==> Enabling and (re)starting the service"
$SUDO systemctl daemon-reload
$SUDO systemctl enable sha-pi-web.service
$SUDO systemctl restart sha-pi-web.service   # restart (not just start) so config/code changes load
sleep 1
$SUDO systemctl --no-pager status sha-pi-web.service | head -12

IP="$(hostname -I | awk '{print $1}')"
echo "==> Web interface should be at http://${IP}/"
