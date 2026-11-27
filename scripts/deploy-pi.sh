#!/usr/bin/env bash
# Deploy the current branch to the Stingray Pi in one command.
#
# Run from the development machine (pathfinder):
#   ./scripts/deploy-pi.sh [branch]        # branch defaults to main
#
# Prerequisites (once, on the Pi):
#   sudo bash /tmp/stingray-permissions-setup.sh
#
# The script refuses to deploy unless the local branch is pushed, so the Pi
# never silently runs an older commit. It pulls, restarts the service, waits
# for a healthy API, and prints the served dashboard asset version.
set -euo pipefail

PI_HOST="${STINGRAY_PI_HOST:-192.168.68.53}"
PI_USER="${STINGRAY_PI_USER:-ravik}"
PI_REPO="${STINGRAY_PI_REPO:-/home/ravik/src_git/project_stingray}"
SSH_KEY="${STINGRAY_PI_KEY:-$HOME/.ssh/id_ed25519_stingray}"
BRANCH="${1:-main}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Checking $BRANCH is pushed"
git fetch --quiet origin
local_head="$(git rev-parse "refs/heads/$BRANCH")"
remote_head="$(git rev-parse "refs/remotes/origin/$BRANCH")"
if [[ "$local_head" != "$remote_head" ]]; then
  echo "ERROR: local $BRANCH ($local_head) differs from origin/$BRANCH ($remote_head)." >&2
  echo "Push your changes first, then re-run this script." >&2
  exit 1
fi
echo "    $local_head is pushed"

echo "==> Deploying $BRANCH to $PI_USER@$PI_HOST"
ssh -o BatchMode=yes -i "$SSH_KEY" "$PI_USER@$PI_HOST" \
  "PI_REPO='$PI_REPO' BRANCH='$BRANCH' bash -s" <<'REMOTE'
set -euo pipefail
umask 0002
cd "$PI_REPO"
echo "-- pulling $BRANCH"
git pull --ff-only origin "$BRANCH"
git log -1 --oneline
echo "-- restarting stingray.service"
sudo -n /usr/bin/systemctl restart stingray.service
for _ in $(seq 1 30); do
  [[ "$(systemctl show stingray.service -p SubState --value)" == "running" ]] && break
  sleep 1
done
systemctl is-active stingray.service
echo "-- health check"
health_ok=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8081/api/health >/dev/null 2>&1; then
    health_ok=1
    break
  fi
  sleep 1
done
[[ "$health_ok" == "1" ]] || { echo "health check FAILED" >&2; exit 1; }
echo "health OK"
echo "-- dashboard asset"
curl -fsS http://127.0.0.1:8081/ | grep -oE 'summary\.js\?v=[0-9]+' | head -1 || true
REMOTE
