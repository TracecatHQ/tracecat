#!/bin/bash

# Bash "strict mode", to help catch problems and bugs in the shell
# script. Every bash script you write should include this. See
# http://redsymbol.net/articles/unofficial-bash-strict-mode/ for details.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# Update package lists
apt-get update

# Install base packages
apt-get install -y \
  acl \
  git \
  xmlsec1 \
  libmagic1

# Install kubectl using the latest official method
# https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/
# Step 1: Install packages needed for Kubernetes repository
apt-get install -y apt-transport-https ca-certificates curl gnupg

# Step 2: Create keyrings directory with proper permissions
mkdir -p -m 755 /etc/apt/keyrings

# Step 3: Download the Kubernetes signing key and store it properly
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.32/deb/Release.key | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
chmod 644 /etc/apt/keyrings/kubernetes-apt-keyring.gpg  # Allow unprivileged APT programs to read this keyring

# Step 4: Add the Kubernetes repository to sources
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.32/deb/ /' | tee /etc/apt/sources.list.d/kubernetes.list
chmod 644 /etc/apt/sources.list.d/kubernetes.list  # Set proper permissions for APT tools

# Step 5: Update package lists and install kubectl
apt-get update
apt-get install -y kubectl

# Apply security updates
apt-get -y upgrade

# Clean up
apt-get clean
rm -rf /var/lib/apt/lists/*
