#!/bin/bash

# Bash "strict mode", to help catch problems and bugs in the shell
# script. Every bash script you write should include this. See
# http://redsymbol.net/articles/unofficial-bash-strict-mode/ for details.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export NSJAIL_VERSION=3.4

# Detect architecture
ARCH=$(uname -m)

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

# Install nsjail build dependencies
echo "Installing nsjail build dependencies..."
apt-get install -y --no-install-recommends \
  gcc \
  g++ \
  make \
  pkg-config \
  bison \
  flex \
  libprotobuf-dev \
  protobuf-compiler \
  libnl-route-3-dev

# Build and install nsjail from source
# Use git clone instead of the release tarball so the submodule init step in the
# Makefile can run without failing on a missing .git directory.
echo "Building nsjail v${NSJAIL_VERSION}..."
NSJAIL_DIR="/tmp/nsjail-${NSJAIL_VERSION}"
git clone --depth 1 --recurse-submodules --branch "${NSJAIL_VERSION}" https://github.com/google/nsjail.git "${NSJAIL_DIR}"
cd "${NSJAIL_DIR}"
make -j"$(nproc)"
install -m 0755 nsjail /usr/local/bin/nsjail
cd /
rm -rf "${NSJAIL_DIR}"

# Verify nsjail installation
if ! nsjail --help >/tmp/nsjail_help.txt 2>&1; then
    echo "ERROR: Failed to install nsjail"
    cat /tmp/nsjail_help.txt
    exit 1
fi
echo "nsjail installed successfully: $(head -n 1 /tmp/nsjail_help.txt)"
rm -f /tmp/nsjail_help.txt

# Create sandbox directories
echo "Creating sandbox directories..."
mkdir -p /var/lib/tracecat/sandbox-rootfs
mkdir -p /var/lib/tracecat/sandbox-cache/packages
mkdir -p /var/lib/tracecat/sandbox-cache/uv-cache
chmod -R 755 /var/lib/tracecat

# Build sandbox rootfs
# The rootfs is a minimal Python 3.12 environment with uv
echo "Building sandbox rootfs..."

# Create base directories
ROOTFS="/var/lib/tracecat/sandbox-rootfs"

# We'll use the current Python installation as a base
# Copy essential directories from the system
echo "Copying system libraries..."
for dir in usr lib lib64 bin sbin etc; do
    if [ -d "/${dir}" ]; then
        mkdir -p "${ROOTFS}/${dir}"
        # Copy files, not symlinks' targets, to preserve structure
        cp -a "/${dir}/." "${ROOTFS}/${dir}/" 2>/dev/null || true
    fi
done

# Create required directories
mkdir -p "${ROOTFS}/tmp"
mkdir -p "${ROOTFS}/proc"
mkdir -p "${ROOTFS}/dev"
mkdir -p "${ROOTFS}/work"
mkdir -p "${ROOTFS}/cache"
mkdir -p "${ROOTFS}/packages"
mkdir -p "${ROOTFS}/home/sandbox"

# Create sandbox user files
echo "sandbox:x:1000:1000:sandbox:/home/sandbox:/bin/sh" >> "${ROOTFS}/etc/passwd"
echo "sandbox:x:1000:" >> "${ROOTFS}/etc/group"

# Set proper permissions on work directories
chown -R 1000:1000 "${ROOTFS}/work" "${ROOTFS}/cache" "${ROOTFS}/packages" "${ROOTFS}/home/sandbox"
chmod 755 "${ROOTFS}/work" "${ROOTFS}/cache" "${ROOTFS}/packages" "${ROOTFS}/home/sandbox"
chmod 1777 "${ROOTFS}/tmp"

# Verify Python is available in the rootfs
if [ ! -x "${ROOTFS}/usr/local/bin/python3" ]; then
    echo "ERROR: Python not found in rootfs"
    exit 1
fi
echo "Python found in rootfs"

# Copy uv binary
echo "Installing uv in rootfs..."
# uv should already be in the image from the base install
if [ -x "/usr/local/bin/uv" ]; then
    cp /usr/local/bin/uv "${ROOTFS}/usr/local/bin/uv"
else
    # Download uv if not present
    curl -fsSL https://astral.sh/uv/install.sh | sh
    cp ~/.local/bin/uv "${ROOTFS}/usr/local/bin/uv" || cp /root/.local/bin/uv "${ROOTFS}/usr/local/bin/uv"
fi
chmod +x "${ROOTFS}/usr/local/bin/uv"
echo "uv installed in rootfs"

echo "Sandbox rootfs built successfully at ${ROOTFS}"

# Create cache directories that will be needed at runtime
mkdir -p \
    /home/apiuser/.cache/uv \
    /home/apiuser/.cache/s3 \
    /home/apiuser/.local \
    /app/.scripts

# Apply security updates
apt-get -y upgrade

# Remove install only dependencies (keep nsjail runtime deps)
apt-get purge -y gcc g++ make pkg-config bison flex gnupg apt-transport-https unzip
apt-get autoremove -y

# Keep runtime dependencies for nsjail
# - libnl-route-3-200: needed for network namespace operations
# - libprotobuf*: needed for nsjail config parsing
# These are kept installed

# Check if git is installed by checking the version
if ! git --version &> /dev/null; then
    echo "ERROR: Failed to install git"
    exit 1
fi

# Clean up
apt-get clean
rm -rf /var/lib/apt/lists/*

echo "Package installation complete"
