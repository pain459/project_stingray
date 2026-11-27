# GitHub release → Pi deployment

This is the deployment model for `stingray`: **GitHub builds one artifact, the Pi
runs exactly that artifact**. The Pi no longer needs a git checkout. Data under
`/mnt/stingray` and credentials under `/etc/stingray` are never part of a release.

## Flow

1. A `v*` tag is pushed (e.g. `v0.2.0`) or `release.yml` is dispatched manually.
2. GitHub Actions builds inside an `linux/arm64` container using the locked
   dependency set (`uv.lock`) and publishes:
   - `stingray-<version>-arm64.tar.gz` — app, config, systemd units and a complete
     arm64 Python 3.13 venv with the `hardware` extra.
   - `stingray-<version>-arm64.tar.gz.sha256`.
3. The Pi's `stingray-deploy.timer` polls the public GitHub Releases API every
   10 minutes. On a new version it verifies the checksum, extracts the tarball
   into `/opt/stingray/releases/<version>`, flips `/opt/stingray/current`,
   restarts `stingray.service`, waits for `/api/health`, and rolls back
   automatically if the service does not come up healthy.
4. The three newest releases are kept for rollback; older ones are deleted.

## Building / evaluating on GitHub first

No Pi changes are made during evaluation. Use manual dispatch with `dry_run`
(the default) to build and verify **without** publishing a Release:

```sh
gh workflow run release.yml -f dry_run=true
gh run watch            # select the newest release run
```

The workflow reports step timings and uploads the artifact as `stingray-dry-run`.
Download and inspect it locally:

```sh
gh run download <run-id> -n stingray-dry-run -D /tmp/opencode/release-check
cd /tmp/opencode/release-check
sha256sum -c ./*.sha256
tar -tzf ./*.tar.gz | head -25
```

Only after the build time, checksum, smoke tests and archive layout are accepted
should a tag be pushed:

```sh
git tag v0.2.0 && git push origin v0.2.0
```

## One-time Pi bootstrap (manual, with sudo)

Run from the existing checkout. This installs the static units and the deploy
script but deliberately does **not** restart the service — the running process
keeps serving until the first release is deployed.

```sh
cd /home/ravik/src_git/project_stingray
sudo SRC_DIR="$PWD" bash scripts/pi-bootstrap.sh
/home/ravik/bin/stingray-deploy poll
/home/ravik/bin/stingray-deploy status
```

`/opt/stingray` is owned `ravik:monusr` with mode `2775`, so file operations need
no sudo. The only sudo used by the deploy script is
`/usr/bin/systemctl restart stingray.service`, already permitted by the narrow
rule in `/etc/sudoers.d/stingray-deploy`.

After the first successful deploy, remove the git checkout from the Pi.

## Day-to-day commands (on the Pi)

```sh
/home/ravik/bin/stingray-deploy status              # current version, sha, service state
/home/ravik/bin/stingray-deploy poll                # force a check now
/home/ravik/bin/stingray-deploy rollback            # flip to the previous release
/home/ravik/bin/stingray-deploy verify <archive>    # checksum + layout check, no install
```

Deployment is automatic via `stingray-deploy.timer`, so normally none of these
are needed.

## Why a prebuilt venv

The Pi is `aarch64` with Python 3.13 and has native wheels (`spidev`, `RPi.GPIO`,
`duckdb`, `pyarrow`, `pillow`) that cannot be produced on an x86 runner without
emulation. Building the venv at its final path inside an arm64 container makes
the deployed bits identical to the evaluated artifact; only `pyvenv.cfg`'s
`home` and the `python*` symlinks are patched from `/usr/local/bin` to
`/usr/bin`. `uv sync --no-editable` keeps the installed package self-contained.

## Constraints / gotchas

- The release asset download must stay unauthenticated, so the GitHub repo must
  remain public. A private repo needs a read-only token on the Pi.
- systemd unit content is static after bootstrap; releasing a new artifact does
  not change unit files. Re-run bootstrap if a unit must change.
- The `hardware` extra now includes `RPi.GPIO` (previously installed manually).
- Release workflow and CI both use Python 3.13 to match the Pi.
- Never run `scripts/validate_gas_sensors.py` while `stingray.service` is active.
