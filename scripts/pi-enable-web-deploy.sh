#!/usr/bin/env bash
# One-time Pi setup for the dashboard "Deploy latest" / "Force redeploy" buttons.
# Run ONCE with sudo on the Pi from a temporary checkout of this repo:
#   sudo SRC_DIR=/path/to/checkout bash scripts/pi-enable-web-deploy.sh
#
# It installs the updated deploy CLI and its one-shot systemd unit, then adds
# a narrow sudoers rule so monusr may start only the two fixed unit instances.
set -euo pipefail

SRC_DIR="${SRC_DIR:?set SRC_DIR to a checkout of project_stingray}"
[[ -d "$SRC_DIR" ]] || { echo "SRC_DIR does not exist: $SRC_DIR" >&2; exit 1; }
[[ "$(id -u)" -eq 0 ]] || { echo "run this script with sudo" >&2; exit 1; }

SYSTEMD=/etc/systemd/system
SUDOERS=/etc/sudoers.d/stingray-web-deploy

echo "==> Installing updated stingray-deploy CLI"
install -d -o ravik -g ravik -m 0755 /home/ravik/bin
install -o ravik -g ravik -m 0755 "$SRC_DIR/scripts/pi-deploy.sh" /home/ravik/bin/stingray-deploy

echo "==> Installing web deploy systemd unit"
install -o root -g root -m 0644 "$SRC_DIR/deploy/stingray-web-deploy@.service" \
  "$SYSTEMD/stingray-web-deploy@.service"

echo "==> Installing narrow sudoers rule"
cat > "$SUDOERS" <<'EOF'
# Web dashboard may start only the two fixed deploy unit instances.
monusr ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block stingray-web-deploy@poll.service
monusr ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block stingray-web-deploy@force.service
# The deploy unit runs as ravik and restarts the service after a health flip.
ravik ALL=(root) NOPASSWD: /usr/bin/systemctl restart stingray.service
EOF
chmod 0440 "$SUDOERS"
visudo -cf "$SUDOERS"

echo "==> Reloading systemd"
systemctl daemon-reload

echo
echo "Web deployment enabled. Verify from pathfinder with:"
echo "  curl -fsS http://192.168.68.53:8081/api/deploy/status"
echo "Requires a release with deploy.enabled=true (v0.2.1 or newer)."
