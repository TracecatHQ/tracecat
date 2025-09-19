#!/bin/bash

# Bash "strict mode", to help catch problems and bugs in the shell
# script. Every bash script you write should include this. See
# http://redsymbol.net/articles/unofficial-bash-strict-mode/ for details.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export DENO_VERSION=2.3.5
export PYODIDE_VERSION=0.27.6

# Detect architecture
ARCH=$(uname -m)
case ${ARCH} in
    x86_64)
        DENO_ARCH="x86_64-unknown-linux-gnu"
        ;;
    aarch64|arm64)
        DENO_ARCH="aarch64-unknown-linux-gnu"
        ;;
    *)
        echo "Unsupported architecture: ${ARCH}"
        exit 1
        ;;
esac

# Update package lists
apt-get update

# Install base packages including curl (needed for kubectl installation)
apt-get install -y \
  acl \
  git \
  xmlsec1 \
  libmagic1 \
  curl \
  apt-transport-https \
  ca-certificates \
  gnupg \
  unzip

# Verify curl is installed and in PATH
which curl || { echo "ERROR: curl not found after installation"; exit 1; }
echo "curl version: $(curl --version | head -n 1)"

# Install Deno
echo "Installing Deno v${DENO_VERSION} for architecture ${ARCH}..."
DENO_ZIP="deno-${DENO_ARCH}.zip"

# Fetch the SHA256 checksum from the official release
CHECKSUM_URL="https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/${DENO_ZIP}.sha256sum"
echo "Fetching SHA256 checksum from ${CHECKSUM_URL}"
DENO_SHA256=$(curl -sSL "${CHECKSUM_URL}" | awk '{print $1}')

if [ -z "${DENO_SHA256}" ]; then
  echo "WARNING: Failed to fetch SHA256 checksum, skipping verification"
  curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/${DENO_ZIP}" -o "${DENO_ZIP}"
else
  echo "Using SHA256 checksum: ${DENO_SHA256}"
  curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/${DENO_ZIP}" -o "${DENO_ZIP}"
  echo "${DENO_SHA256}  ${DENO_ZIP}" | sha256sum -c -
fi

# Install deno
unzip -o "${DENO_ZIP}" -d /usr/local/bin/
rm "${DENO_ZIP}"
chmod +x /usr/local/bin/deno

# Verify deno installation
if ! deno --version; then
    echo "ERROR: Failed to install deno"
    exit 1
fi
echo "Deno installed successfully"

# Pre-cache pyodide and dependencies using deno cache
echo "Pre-caching Pyodide v${PYODIDE_VERSION}..."

# Create ALL cache directories that apiuser will need
# This consolidates directory creation in one place
# Note: Permissions will be set in Dockerfile after user creation
mkdir -p \
    /home/apiuser/.cache/deno \
    /home/apiuser/.cache/uv \
    /home/apiuser/.cache/pyodide-packages \
    /home/apiuser/.cache/s3 \
    /home/apiuser/.local \
    /home/apiuser/.local/lib/node_modules \
    /app/.scripts

# Set DENO_DIR for caching during build (use root-owned location)
export DENO_DIR="/opt/deno-cache"
mkdir -p "$DENO_DIR"

# Use deno cache to download pyodide module and its dependencies
# This runs as root and creates root-owned cache that will be copied later
# Note: node_modules will be created automatically in the current directory
cd /opt
deno cache --node-modules-dir=auto "npm:pyodide@${PYODIDE_VERSION}"

echo "Deno and Pyodide installation complete"

# Apply security updates
apt-get -y upgrade

# Remove install only dependencies
apt-get purge -y curl gnupg apt-transport-https unzip
apt-get autoremove -y

# Check if git is installed by checking the version
if ! git --version &> /dev/null; then
    echo "ERROR: Failed to install git"
    exit 1
fi

# Clean up
apt-get clean
rm -rf /var/lib/apt/lists/*
