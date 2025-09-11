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

# Install base packages including curl (needed for kubectl and Claude Code installation)
apt-get install -y \
  acl \
  git \
  xmlsec1 \
  libmagic1 \
  curl \
  apt-transport-https \
  ca-certificates \
  gnupg \
  unzip \
  ripgrep

# Verify curl is installed and in PATH
which curl || { echo "ERROR: curl not found after installation"; exit 1; }
echo "curl version: $(curl --version | head -n 1)"

# Install Claude Code as a native binary
# This method doesn't require Node.js and is more efficient
echo "Installing Claude Code native binary..."

# Create a temporary directory for the installation
TEMP_INSTALL_DIR="/tmp/claude-install"
mkdir -p "$TEMP_INSTALL_DIR"
cd "$TEMP_INSTALL_DIR"

# Download and execute the official installation script with security checks
# Security features:
# - Verify curl succeeded before piping to bash
# - Use set -euo pipefail for the subshell
# - Install to a specific location for better control
echo "Downloading Claude Code installation script..."
INSTALL_SCRIPT_URL="https://claude.ai/install.sh"

# First, download the script to inspect/verify
if curl -fsSL "$INSTALL_SCRIPT_URL" -o install-claude.sh; then
    echo "Installation script downloaded successfully"

    # Make the script executable
    chmod +x install-claude.sh

    # Run the installation script
    # Note: The script automatically detects architecture (x86_64/arm64)
    # The script installs to ~/.local/bin by default
    bash install-claude.sh || { echo "ERROR: Failed to install Claude Code binary"; exit 1; }

    # Move the binary to a system-wide location
    # The installation script puts it in ~/.local/bin/claude
    if [ -f "$HOME/.local/bin/claude" ]; then
        echo "Moving Claude binary to /usr/local/bin..."
        mv "$HOME/.local/bin/claude" /usr/local/bin/claude
        chmod +x /usr/local/bin/claude
    else
        echo "ERROR: Claude binary not found at expected location after installation"
        exit 1
    fi
else
    echo "ERROR: Failed to download Claude Code installation script"
    exit 1
fi

# Clean up temporary installation directory
cd /
rm -rf "$TEMP_INSTALL_DIR"

# Verify installation
if command -v claude &> /dev/null; then
    echo "Claude Code binary installed successfully at $(which claude)"
    echo "Claude version: $(claude --version 2>/dev/null || echo 'version command not available')"
else
    echo "ERROR: Claude Code binary not found in PATH after installation"
    exit 1
fi

# Install kubectl using the latest official method
# https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/

# Step 1: Create keyrings directory with proper permissions
mkdir -p -m 755 /etc/apt/keyrings

# Step 2: Download the Kubernetes signing key and store it properly
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.32/deb/Release.key | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg || { echo "ERROR: Failed to download and process Kubernetes key"; exit 1; }
chmod 644 /etc/apt/keyrings/kubernetes-apt-keyring.gpg  # Allow unprivileged APT programs to read this keyring

# Step 3: Add the Kubernetes repository to sources
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.32/deb/ /' | tee /etc/apt/sources.list.d/kubernetes.list || { echo "ERROR: Failed to add Kubernetes repository"; exit 1; }
chmod 644 /etc/apt/sources.list.d/kubernetes.list  # Set proper permissions for APT tools

# Step 4: Update package lists and install kubectl
apt-get update
apt-get install -y kubectl

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
# Note: Keeping curl and ca-certificates as they may be needed by other tools at runtime
apt-get purge -y gnupg apt-transport-https unzip
apt-get autoremove -y

# Check if git is installed by checking the version
if ! git --version &> /dev/null; then
    echo "ERROR: Failed to install git"
    exit 1
fi

# Check if kubectl is installed by checking the version
if ! kubectl version --client &> /dev/null; then
    echo "ERROR: Failed to install kubectl"
    exit 1
fi

# Check if ripgrep is installed by checking the version
if ! rg --version &> /dev/null; then
    echo "ERROR: Failed to install ripgrep"
    exit 1
fi

# Check if Claude Code is installed by checking the command
if ! command -v claude &> /dev/null; then
    echo "ERROR: Failed to install Claude Code"
    exit 1
fi

# Clean up
apt-get clean
rm -rf /var/lib/apt/lists/*
