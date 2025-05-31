#!/bin/bash

# Bash "strict mode", to help catch problems and bugs in the shell
# script. Every bash script you write should include this. See
# http://redsymbol.net/articles/unofficial-bash-strict-mode/ for details.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# Version configuration with defaults
# These values come from Docker ENV variables, which are also used by the application via config.py
DENO_VERSION="${DENO_VERSION:-2.1.4}"
PYODIDE_VERSION="${PYODIDE_VERSION:-0.26.4}"
# Checksum for deno - this needs to be updated when DENO_VERSION changes
DENO_SHA256="${DENO_SHA256:-3e8b6e153879b3e61ad9c8df77b07c26b95f859308cf34fe87d17ea2ba9c4b4e}"

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
echo "Installing Deno v${DENO_VERSION}..."
DENO_ZIP="deno-x86_64-unknown-linux-gnu.zip"
curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/${DENO_ZIP}" -o "${DENO_ZIP}"

# Verify checksum
echo "${DENO_SHA256}  ${DENO_ZIP}" | sha256sum -c -

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

# Create cache directory that will be accessible by apiuser
export DENO_DIR="/opt/deno-cache"
mkdir -p "${DENO_DIR}"

# Use deno cache to download pyodide module and its dependencies
# This is simpler and more secure than running a script
deno cache --node-modules-dir=/opt/node_modules "npm:pyodide@${PYODIDE_VERSION}"

# Set read-only permissions for security (apiuser can read but not modify)
chmod -R 755 /opt/node_modules
chmod -R 755 "${DENO_DIR}"

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

# Check if kubectl is installed by checking the version
if ! kubectl version --client &> /dev/null; then
    echo "ERROR: Failed to install kubectl"
    exit 1
fi

# Clean up
apt-get clean
rm -rf /var/lib/apt/lists/*
