#!/usr/bin/env bash
# install-packages.sh
set -euo pipefail

# ---- Versions (override via build args/env if needed) ----
: "${DENO_VERSION:=2.3.5}"
: "${PYODIDE_VERSION:=0.27.6}"

# ---- Arch detection for Deno artifact ----
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64)          DENO_ARCH="x86_64-unknown-linux-gnu" ;;
  aarch64|arm64)   DENO_ARCH="aarch64-unknown-linux-gnu" ;;
  *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;;
esac

DENO_ZIP="deno-${DENO_ARCH}.zip"
BASE_URL="https://github.com/denoland/deno/releases/download/v${DENO_VERSION}"
CHECKSUM_URL="${BASE_URL}/${DENO_ZIP}.sha256sum"
DENO_URL="${BASE_URL}/${DENO_ZIP}"

# ---- Cleanup on exit ----
cleanup() {
  rm -f "${DENO_ZIP}.partial" || true
}
trap cleanup EXIT

# ---- wget helpers (quiet, with a few retries) ----
wget_stdout() { wget -q --tries=3 --timeout=20 -O- "$1"; }
wget_to()     { wget -q --tries=3 --timeout=20 "$1" -O "$2"; }

echo "Installing Deno v${DENO_VERSION} for ${ARCH} …"
echo "Fetching checksum: ${CHECKSUM_URL}"
DENO_SHA256="$(wget_stdout "${CHECKSUM_URL}" | awk '{print $1}' || true)"

echo "Downloading: ${DENO_URL}"
# Download to a temp name to avoid half-written files if interrupted
wget_to "${DENO_URL}" "${DENO_ZIP}.partial"
mv "${DENO_ZIP}.partial" "${DENO_ZIP}"

if [[ -n "${DENO_SHA256}" ]]; then
  echo "${DENO_SHA256}  ${DENO_ZIP}" | sha256sum -c -
else
  echo "WARNING: checksum unavailable; skipping verification." >&2
fi

# Install Deno
unzip -o "${DENO_ZIP}" -d /usr/local/bin/
chmod +x /usr/local/bin/deno
rm -f "${DENO_ZIP}"

# Verify install
if ! deno --version >/dev/null 2>&1; then
  echo "ERROR: Deno failed to install." >&2
  exit 1
fi
echo "Deno installed successfully."

# ---- Pre-cache Pyodide with Deno (builder layer only) ----
echo "Pre-caching Pyodide v${PYODIDE_VERSION} …"

# Create runtime dirs that the final image expects (ownership fixed later)
mkdir -p \
  /home/nonroot/.cache/deno \
  /home/nonroot/.cache/uv \
  /home/nonroot/.cache/pyodide-packages \
  /home/nonroot/.cache/s3 \
  /home/nonroot/.cache/tmp \
  /home/nonroot/.local/lib/node_modules \
  /app/.scripts

# Use a root-owned build cache for Deno in the builder layer
export DENO_DIR="/opt/deno-cache"
mkdir -p "${DENO_DIR}"

# Place node_modules under /opt so we can selectively COPY to final if needed
pushd /opt >/dev/null
deno cache --node-modules-dir=auto "npm:pyodide@${PYODIDE_VERSION}"
popd >/dev/null

echo "Deno + Pyodide setup complete."