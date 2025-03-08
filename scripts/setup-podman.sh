#!/bin/bash
set -euo pipefail

# Install minimal Podman dependencies
apt-get update
apt-get install -y \
  podman \
  crun \
  uidmap

# Create necessary directories with proper permissions
mkdir -p /etc/containers
mkdir -p /etc/subuid /etc/subgid
mkdir -p /run/podman
mkdir -p /run/containers/storage
mkdir -p /var/lib/containers/storage
mkdir -p /var/lib/containers/storage/volumes

# Copy configuration files
cp /app/config/podman/containers.conf /etc/containers/containers.conf
cp /app/config/podman/storage.conf /etc/containers/storage.conf
cp /app/config/podman/seccomp.json /etc/containers/seccomp.json

# === Setup rootless podman === #

# 1. Remap user namespaces
echo "apiuser:100000:65536" >> /etc/subuid
echo "apiuser:100000:65536" >> /etc/subgid

# 2. Set secure permissions for all required directories
chmod 750 /run/podman
chmod 644 /etc/containers/seccomp.json
chmod -R 770 /var/lib/containers/storage
chmod -R 770 /run/containers/storage

# 3. Set ownership for storage directories
chown -R root:apiuser /var/lib/containers/storage
chown -R root:apiuser /run/containers/storage
chown root:apiuser /run/podman

# Cleanup
rm -rf /var/lib/apt/lists/*
