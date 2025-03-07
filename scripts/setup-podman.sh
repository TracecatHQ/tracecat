#!/bin/bash
set -euo pipefail

# Install minimal Podman dependencies
apt-get update
apt-get install -y \
  podman \
  crun \
  uidmap

# Configure Podman environment with secure defaults
mkdir -p /etc/containers
cat > /etc/containers/containers.conf << EOF
[containers]
# No default capabilities
default_capabilities = []
default_sysctls = []
# Secure volume mounts by default
default_mount_options = ["nodev", "nosuid", "noexec"]
# Prevent privilege escalation
no_new_privileges = true

[engine]
runtime = "crun"
userns_mode = "auto"
cgroup_manager = "cgroupfs"
# Enable automatic volume cleanup
volume_cleanup = "true"

[engine.runtimes]
crun = [
  "/usr/bin/crun",
]
EOF

# Set up user namespace remapping
mkdir -p /etc/subuid /etc/subgid
echo "apiuser:100000:65536" >> /etc/subuid
echo "apiuser:100000:65536" >> /etc/subgid

# Minimal storage configuration - only for named volumes
mkdir -p /etc/containers
cat > /etc/containers/storage.conf << EOF
[storage]
# Only need runtime and volume storage
runroot = "/run/containers/storage"
graphroot = "/var/lib/containers/storage"
[storage.options]
# Block additional image stores
additionalimagestores = []
EOF

# Strict seccomp policy
cat > /etc/containers/seccomp.json << EOF
{
    "defaultAction": "SCMP_ACT_ERRNO",
    "architectures": [
        "SCMP_ARCH_X86_64"
    ],
    "syscalls": [
        {
            "names": [
                "read",
                "write",
                "open",
                "close",
                "exit",
                "exit_group"
            ],
            "action": "SCMP_ACT_ALLOW"
        }
    ]
}
EOF

# Create podman socket with minimal permissions
mkdir -p /run/podman
chmod 750 /run/podman

# Create volume directory with secure permissions
mkdir -p /var/lib/containers/storage/volumes
chown root:apiuser /var/lib/containers/storage/volumes
chmod 770 /var/lib/containers/storage/volumes

# Cleanup
rm -rf /var/lib/apt/lists/*
