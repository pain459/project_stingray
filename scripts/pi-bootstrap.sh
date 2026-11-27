#!/usr/bin/env bash
# One-time Pi bootstrap for the release-based deployment model.
# Run ONCE with sudo, from the existing checkout, e.g.:
#   sudo SRC_DIR=/path/to/temporary/checkout bash scripts/pi-bootstrap.sh
#
# Prepares /opt/stingray and installs static systemd units pointing at
# /opt/stingray/current. It deliberately does NOT restart stingray.service:
# the currently running process keeps serving until the first release is
# deployed by `stingray-deploy poll`, which performs the restart.
set -euo pipefail

SRC_DIR="${SRC_DIR:?set SRC_DIR to the current checkout}"
[[ -d "$SRC_DIR" ]] || { echo "SRC_DIR does not exist: $SRC_DIR" >&2; exit 1; }
[[ "$(id -u)" -eq 0 ]] || { echo "run this script with sudo" >&2; exit 1; }

BASE=/opt/stingray
DEPLOY_HOME=/home/ravik/bin
SYSTEMD=/etc/systemd/system

echo "==> Creating $BASE"
install -d -o ravik -g monusr -m 2775 "$BASE" "$BASE/releases" "$BASE/downloads"
chmod 2775 "$BASE" "$BASE/releases" "$BASE/downloads"

echo "==> Installing stingray-deploy"
install -d -o ravik -g ravik -m 0755 "$DEPLOY_HOME"
install -o ravik -g ravik -m 0755 "$SRC_DIR/scripts/pi-deploy.sh" "$DEPLOY_HOME/stingray-deploy"

echo "==> Installing systemd units"
for unit in stingray.service stingray-hourly-summary.service stingray-hourly-summary.timer \
  stingray-deploy.service stingray-deploy.timer; do
  install -o root -g root -m 0644 "$SRC_DIR/deploy/$unit" "$SYSTEMD/$unit"
done
# Keep any existing Telegram drop-in; do not overwrite credentials or env wiring.
install -d -o root -g root -m 0755 "$SYSTEMD/stingray.service.d"
if [[ ! -e "$SYSTEMD/stingray.service.d/telegram.conf" ]]; then
  install -o root -g root -m 0644 "$SRC_DIR/deploy/stingray-telegram.conf" \
    "$SYSTEMD/stingray.service.d/telegram.conf"
fi

echo "==> Reloading systemd, enabling and starting timers"
systemctl daemon-reload
systemctl enable stingray.service
systemctl enable stingray-hourly-summary.timer
systemctl enable stingray-deploy.timer
# `enable` only wires the symlinks; the timer must also be started to poll now.
systemctl start stingray-hourly-summary.timer
systemctl start stingray-deploy.timer

echo
echo "Bootstrap complete. The running service is untouched."
echo "Next: deploy the evaluated release with:"
echo "  /home/ravik/bin/stingray-deploy poll"
echo "Then check:"
echo "  /home/ravik/bin/stingray-deploy status"
