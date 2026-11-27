#!/usr/bin/env bash
# Build a relocatable, self-contained arm64 Stingray release.
#
# This script runs INSIDE an linux/arm64 container (see .github/workflows/release.yml),
# with the checkout mounted read-only at /src and the output directory at /dist.
# The venv is created at its final path so console-script shebangs are correct;
# only the absolute Python home/symlinks need fixing up for the Pi.
set -euo pipefail

VERSION="${1:?usage: build-release.sh <version> <git-sha>}"
BUILD_SHA="${2:?usage: build-release.sh <version> <git-sha>}"
SRC=/src
DIST=/dist
RELEASES=/opt/stingray/releases
TARGET="$RELEASES/$VERSION"
PY_NAME="$(python -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')"

echo "==> Installing build toolchain"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# RPi.GPIO and spidev are sdists; duckdb/pyarrow/pillow use prebuilt aarch64 wheels.
apt-get install -y -qq --no-install-recommends build-essential >/dev/null
python -m pip install --quiet --no-cache-dir uv

echo "==> Staging release at $TARGET"
mkdir -p "$TARGET" "$DIST"
cp -a "$SRC/src" "$SRC/config" "$SRC/deploy" \
  "$SRC/pyproject.toml" "$SRC/uv.lock" "$SRC/README.md" "$TARGET/"
printf '%s\n' "$VERSION" > "$TARGET/VERSION"
printf '%s\n' "$BUILD_SHA" > "$TARGET/BUILD_SHA"

echo "==> Creating locked venv (python $PY_NAME, hardware extra, non-editable)"
cd "$TARGET"
uv venv .venv --python "$(command -v python)"
uv sync --frozen --no-dev --extra hardware --no-editable

echo "==> Relocating venv Python paths for the Pi (/usr/bin/$PY_NAME)"
sed -i "s#^home = .*#home = /usr/bin#" .venv/pyvenv.cfg
ln -sfn "/usr/bin/$PY_NAME" .venv/bin/python
ln -sfn "/usr/bin/$PY_NAME" ".venv/bin/$PY_NAME"
if [[ -e .venv/bin/python3 ]]; then
  ln -sfn "/usr/bin/$PY_NAME" .venv/bin/python3
fi

# The container's lib dir is python3.13 but the installed package is pure Python;
# compiled extensions are inside site-packages and are already aarch64.
find "$TARGET" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$TARGET" -type d \( -name .pytest_cache -o -name .ruff_cache -o -name .mypy_cache \) -prune -exec rm -rf {} +

echo "==> Smoke testing the built venv"
.venv/bin/python - <<'PY'
from stingray.core.config import load_config
cfg = load_config("config/application.combined.yaml")
print(f"config OK: mode={cfg.mode}, api_port={cfg.api.port}")
PY
.venv/bin/python - <<'PY'
import duckdb, pyarrow, spidev  # noqa: F401
print("native imports OK")
PY
# RPi.GPIO refuses to import off a Pi, so verify its compiled aarch64 extension.
find .venv/lib -name '_GPIO*.so' -print -quit | grep -q . \
  || { echo "RPi.GPIO extension missing" >&2; exit 1; }
echo "RPi.GPIO extension present"

echo "==> Packaging"
cd "$RELEASES"
ARCHIVE="stingray-$VERSION-arm64.tar.gz"
tar -czf "$DIST/$ARCHIVE" "$VERSION"
cd "$DIST"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
cat "$ARCHIVE.sha256"
echo "==> Built $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
