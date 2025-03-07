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
cat > /etc/containers/containers.conf << EOF
[containers]
default_capabilities = []
default_sysctls = []

[engine]
runtime = "crun"
userns_mode = "auto"
cgroup_manager = "cgroupfs"

[engine.runtimes]
crun = [
  "/usr/bin/crun",
]
EOF

# Set up user namespace remapping
mkdir -p /etc/subuid /etc/subgid
echo "apiuser:100000:65536" >> /etc/subuid
echo "apiuser:100000:65536" >> /etc/subgid

# Create storage configuration
mkdir -p /etc/containers
cat > /etc/containers/storage.conf << EOF
[storage]
driver = "overlay"
runroot = "/run/containers/storage"
graphroot = "/var/lib/containers/storage"
[storage.options]
additionalimagestores = []
[storage.options.overlay]
mount_program = "/usr/bin/fuse-overlayfs"
mountopt = "nodev,fsync=0"
EOF

# Create podman socket directory with proper permissions
mkdir -p /run/podman
chmod 750 /run/podman

# Cleanup
rm -rf /var/lib/apt/lists/*
