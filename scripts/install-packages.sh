#!/bin/bash

# Bash "strict mode", to help catch problems and bugs in the shell
# script. Every bash script you write should include this. See
# http://redsymbol.net/articles/unofficial-bash-strict-mode/ for details.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

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
  gnupg

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

# Apply security updates
apt-get -y upgrade

# Remove install only dependencies
apt-get purge -y curl gnupg apt-transport-https
apt-get autoremove -y

# Check if git is installed by checking the version
if ! git --version &> /dev/null; then
    echo "ERROR: Failed to install git"
    exit 1
fi

# Check if kubectl is installed by checking the version
if ! kubectl --version &> /dev/null; then
    echo "ERROR: Failed to install kubectl"
    exit 1
fi

# Clean up
apt-get clean
rm -rf /var/lib/apt/lists/*
