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
   30 minutes. On a new version it verifies the checksum, extracts the tarball
   into `/opt/stingray/releases/<version>`, flips `/opt/stingray/current`,
   restarts `stingray.service`, waits for `/api/health`, and rolls back
   automatically if the service does not come up healthy.
4. The three newest releases are kept for rollback; older ones are deleted.

## Current deployed state (2026-11-27)

- Release `v0.2.0`, build sha `64af31a4d75a095031b0ccaeca958894f123cbb9`,
  installed at `/opt/stingray/releases/0.2.0` via `/opt/stingray/current`.
- **The Pi has no git checkout.** Code, config and the venv come only from the
  release tarball. Data under `/mnt/stingray` and credentials under
  `/etc/stingray` are host-owned and never part of a release.
- Host prerequisites (service user, groups, fonts, data/log directories, static
  systemd units, `/etc/sudoers.d/stingray-deploy`) were installed once by
  `scripts/pi-bootstrap.sh` and are not changed by a release.

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

## Cutting a release (standard process)

All release work happens on `pathfinder`; the Pi is never a build host.

1. **Prepare.** Land and push the changes on `main`. Confirm the working tree
   is clean and tests pass:

   ```sh
   uv run pytest -q
   uv lock --check
   ```

2. **Bump the version** in `pyproject.toml`, refresh the lockfile, commit and
   push:

   ```sh
   uv lock
   git commit -am "chore: bump version to <version>" && git push origin main
   ```

3. **Evaluate the build** on GitHub without publishing anything (see the
   previous section). Check the build duration, SHA256, archive layout,
   `version_info = 3.13.5`, aarch64 native extensions and absence of secrets.

4. **Publish** by tagging the evaluated commit. Never re-tag or replace an
   existing asset — fix forward with a new patch version instead:

   ```sh
   git tag v<version> && git push origin v<version>
   gh run watch            # select the new release run
   gh release view v<version> --json assets
   ```

5. **Verify on the Pi** (within 30 minutes automatically, or force it):

   ```sh
   /home/ravik/bin/stingray-deploy poll
   /home/ravik/bin/stingray-deploy status
   curl -fsS http://127.0.0.1:8081/api/health | jq
   ```

   Check `mode: hardware`, zero `reads_failed` for `sht45`, `sgp41`, `scd4x` and
   `sps30`, and the served `summary.js?v=N`. The deploy itself is health-gated
   and rolls back automatically if the service does not come up cleanly.

6. **If health fails:** `/home/ravik/bin/stingray-deploy rollback`, then fix
   forward with a new release.

## One-time Pi bootstrap (already completed 2026-11-27)

This was done at cutover and does **not** need repeating for normal releases.
It required interactive sudo because only one narrow restart command is
passwordless. It created `/opt/stingray` (owned `ravik:monusr`, mode `2775`),
installed `/home/ravik/bin/stingray-deploy`, installed the static units and
started the timers, without restarting the running service. The checkout used
to run it has since been removed from the Pi.

For disaster recovery, fetch `scripts/` and `deploy/` from GitHub to a
temporary directory on the Pi and run:

```sh
sudo SRC_DIR=/path/to/temporary/checkout bash scripts/pi-bootstrap.sh
/home/ravik/bin/stingray-deploy poll
/home/ravik/bin/stingray-deploy status
```

Bootstrap now both enables **and starts** the timers. The only sudo used by the
deploy script is `/usr/bin/systemctl restart stingray.service`, permitted by the
narrow rule in `/etc/sudoers.d/stingray-deploy`.

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

The Pi is `aarch64` with Python 3.13.5 and has native wheels (`spidev`, `RPi.GPIO`,
`duckdb`, `pyarrow`, `pillow`) that cannot be produced on an x86 runner without
emulation. Building the venv at its final path inside an arm64 container makes
the deployed bits identical to the evaluated artifact; only `pyvenv.cfg`'s
`home` and the `python*` symlinks are patched from `/usr/local/bin` to
`/usr/bin`. `uv sync --no-editable` keeps the installed package self-contained.

## Constraints / gotchas

- The release asset download must stay unauthenticated, so the GitHub repo must
  remain public. A private repo needs a read-only token on the Pi.
- systemd unit content is static after bootstrap; releasing a new artifact does
  not change unit files. To change a unit on the Pi (for example the poll
  interval), copy the updated file from `deploy/` in GitHub into
  `/etc/systemd/system/`, then `sudo systemctl daemon-reload` and restart the
  affected timer or service. The polling interval is currently
  `OnCalendar=*:0/30`.
- The `hardware` extra now includes `RPi.GPIO` (previously installed manually).
- Release workflow and CI both use Python 3.13 to match the Pi.
- Never run `scripts/validate_gas_sensors.py` while `stingray.service` is active.
