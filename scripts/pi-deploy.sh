#!/usr/bin/env bash
# stingray-deploy — install/verify/roll back GitHub releases on the Pi.
# Installed once at /home/ravik/bin/stingray-deploy by pi-bootstrap.sh.
# Only the service restart uses sudo (covered by the existing narrow rule).
set -euo pipefail

BASE=/opt/stingray
RELEASES="$BASE/releases"
DOWNLOADS="$BASE/downloads"
CURRENT="$BASE/current"
LOCK="$BASE/.deploy.lock"
API="https://api.github.com/repos/pain459/project_stingray/releases/latest"
HEALTH="http://127.0.0.1:8081/api/health"
KEEP=3

# The web dashboard sets STINGRAY_DEPLOY_STATUS through its one-shot unit so it
# can poll the final result while stingray.service is restarting.
STATUS_FILE="${STINGRAY_DEPLOY_STATUS:-}"
ACTION="status"
STARTED_AT=""
STARTED_EPOCH=""
RESULT_MESSAGE="Deployment completed"
LOCK_SKIPPED=0

log() { printf '[stingray-deploy] %s\n' "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

write_status() {
  [[ -n "$STATUS_FILE" ]] || return 0
  command -v jq >/dev/null 2>&1 || return 0
  local state="$1" rc="$2"
  if jq -nc \
    --arg state "$state" --arg action "$ACTION" --arg message "$RESULT_MESSAGE" \
    --arg version "$(current_version)" --arg started_at "$STARTED_AT" \
    --arg finished_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --argjson started_epoch "${STARTED_EPOCH:-0}" --argjson exit_code "$rc" \
    '{state:$state,action:$action,message:$message,version:$version,
      started_at:$started_at,finished_at:$finished_at,
      started_epoch:$started_epoch,exit_code:$exit_code}' \
    > "$STATUS_FILE.tmp"; then
    mv -f "$STATUS_FILE.tmp" "$STATUS_FILE" || true
  else
    rm -f "$STATUS_FILE.tmp"
  fi
  return 0
}

finalize() {
  local rc=$?
  trap - EXIT
  if [[ "$LOCK_SKIPPED" == "1" ]]; then
    RESULT_MESSAGE="Another deployment is already running"
    write_status error 0
  elif [[ "$rc" -eq 0 ]]; then
    write_status success 0
  else
    RESULT_MESSAGE="Deployment failed; see journalctl -u stingray-deploy.service"
    write_status error "$rc"
  fi
  exit "$rc"
}

current_version() {
  [[ -e "$CURRENT/VERSION" ]] && cat "$CURRENT/VERSION" || echo "none"
}

wait_healthy() {
  local wait=$1
  for _ in $(seq 1 "$wait"); do
    if curl -fsS "$HEALTH" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}

restart_service() {
  sudo -n /usr/bin/systemctl restart stingray.service || return 1
  wait_healthy 40 || return 1
}

flip_to() {
  local target="$1"
  [[ -d "$target" ]] || die "release directory missing: $target"
  ln -sfn "$target" "$BASE/current.new"
  mv -Tf "$BASE/current.new" "$CURRENT"
}

cleanup_old() {
  # Keep the KEEP newest release directories; never touch the active one.
  local active keep_list victim
  active="$(readlink -f "$CURRENT" || true)"
  keep_list="$(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -Vr | head -n "$KEEP")"
  for victim in $(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -Vr); do
    case "$keep_list" in *"$victim"*) continue ;; esac
    [[ "$RELEASES/$victim" == "$active" ]] && continue
    log "removing old release $victim"
    rm -rf "$RELEASES/$victim"
  done
}

do_install() {
  local version="$1" archive_url="$2" sha_url="$3"
  local archive="$DOWNLOADS/stingray-$version-arm64.tar.gz"
  local sha_file="$archive.sha256"
  local previous

  log "downloading release $version"
  curl -fsSL --retry 3 -o "$archive" "$archive_url" || die "download failed: $archive_url"
  curl -fsSL --retry 3 -o "$sha_file" "$sha_url" || die "download failed: $sha_url"
  (cd "$DOWNLOADS" && sha256sum -c "$(basename "$sha_file")") || die "checksum verification failed"

  previous="$(readlink -f "$CURRENT" || true)"
  rm -rf "$RELEASES/$version"
  mkdir -p "$RELEASES/$version"
  tar -xzf "$archive" -C "$RELEASES" --no-same-owner

  local installed
  installed="$(cat "$RELEASES/$version/VERSION")"
  [[ "$installed" == "$version" ]] || die "VERSION file mismatch: $installed != $version"

  log "flipping current to $version"
  flip_to "$RELEASES/$version"
  if ! restart_service; then
    log "deployment failed; rolling back"
    [[ -n "$previous" ]] && flip_to "$previous"
    restart_service || log "rollback restart failed — manual intervention required"
    die "deployment of $version failed"
  fi

  printf '%s\n' "$version" > "$BASE/deployed_version"
  cleanup_old
  log "deployed $version successfully"
}

# do_poll_forced 0 = ordinary poll (skip when already current)
# do_poll_forced 1 = force reinstall of the latest release at the same version
do_poll_forced() {
  local forced="$1"
  local json tag version archive_url sha_url
  json="$(curl -fsSL --retry 2 "$API")" || die "GitHub API request failed"
  tag="$(printf '%s' "$json" | jq -r '.tag_name // empty')"
  [[ -n "$tag" ]] || die "no tag_name in GitHub API response"
  version="${tag#v}"
  if [[ "$forced" == "0" && "$version" == "$(current_version)" ]]; then
    log "already on $version"
    RESULT_MESSAGE="Already on $version (no change)"
    return 0
  fi
  [[ "$forced" == "1" ]] && log "force reinstalling $version"
  archive_url="$(printf '%s' "$json" | jq -r --arg v "$version" \
    '.assets[] | select(.name == "stingray-\($v)-arm64.tar.gz") | .browser_download_url')"
  sha_url="$(printf '%s' "$json" | jq -r --arg v "$version" \
    '.assets[] | select(.name == "stingray-\($v)-arm64.tar.gz.sha256") | .browser_download_url')"
  [[ -n "$archive_url" && -n "$sha_url" ]] || die "release $version is missing arm64 assets"
  do_install "$version" "$archive_url" "$sha_url"
}

do_rollback() {
  local previous
  previous="$(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -Vr | sed -n '2p')"
  [[ -n "$previous" ]] || die "no previous release to roll back to"
  log "rolling back to $previous"
  flip_to "$RELEASES/$previous"
  restart_service || die "rollback failed"
  printf '%s\n' "$previous" > "$BASE/deployed_version"
  log "rolled back to $previous"
}

do_status() {
  log "current: $(current_version)"
  [[ -e "$CURRENT/VERSION" ]] && cat "$CURRENT/VERSION"
  [[ -e "$CURRENT/BUILD_SHA" ]] && { printf 'build sha: '; cat "$CURRENT/BUILD_SHA"; }
  log "service: $(systemctl is-active stingray.service)"
  log "releases: $(find "$RELEASES" -mindepth 1 -maxdepth 1 -type d -printf '%f ' | sort -Vr)"
}

do_verify() {
  local archive="$1"
  local sha_file="$archive.sha256"
  [[ -f "$archive" && -f "$sha_file" ]] || die "archive or checksum missing"
  (cd "$(dirname "$archive")" && sha256sum -c "$(basename "$sha_file")") || die "checksum failed"
  local tmp
  tmp="$(mktemp -d)"
  tar -xzf "$archive" -C "$tmp"
  find "$tmp" -mindepth 1 -maxdepth 1 -type d -printf 'release dir: %f\n'
  local v
  v="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d -printf '%f' | head -1)"
  log "version: $(cat "$tmp/$v/VERSION") sha: $(cat "$tmp/$v/BUILD_SHA")"
  test -x "$tmp/$v/.venv/bin/python" && log "venv python present"
  rm -rf "$tmp"
  log "verify OK"
}

main() {
  local action="${1:-status}"
  mkdir -p "$RELEASES" "$DOWNLOADS"
  ACTION="$action"
  STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  STARTED_EPOCH="$(date +%s)"
  trap finalize EXIT

  exec 9>"$LOCK"
  flock -n 9 || { log "another deploy is running"; LOCK_SKIPPED=1; exit 0; }

  case "$action" in
    poll) do_poll_forced 0 ;;
    force) do_poll_forced 1 ;;
    install) do_install "${2:?version}" "${3:?archive url}" "${4:?sha url}" ;;
    rollback) do_rollback ;;
    status) do_status ;;
    verify) do_verify "${2:?archive path}" ;;
    *) die "usage: stingray-deploy {poll|force|install|rollback|status|verify <archive>}" ;;
  esac
}

main "$@"
