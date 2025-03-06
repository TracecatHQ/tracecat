#!/bin/bash
set -euo pipefail

# Install Podman and dependencies
apt-get update
apt-get install -y \
  podman \
  crun \
  fuse-overlayfs \
  slirp4netns \
  uidmap

# Configure Podman environment
mkdir -p /etc/containers
echo '[engine]' > /etc/containers/containers.conf
echo 'runtime = "crun"' >> /etc/containers/containers.conf

# Create podman socket directory with proper permissions
mkdir -p /run/podman
chmod 750 /run/podman

# Cleanup
rm -rf /var/lib/apt/lists/*
