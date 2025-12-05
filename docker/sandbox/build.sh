#!/usr/bin/env bash
# Build and extract the sandbox rootfs for nsjail Python execution
#
# This script:
# 1. Builds the sandbox Docker image
# 2. Creates a container from the image
# 3. Exports the container filesystem to the rootfs directory
# 4. Cleans up the temporary container
#
# Usage: ./build.sh [ROOTFS_PATH]
#   ROOTFS_PATH: Optional path to extract rootfs to (default: /var/lib/tracecat/sandbox-rootfs)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOTFS_PATH="${1:-/var/lib/tracecat/sandbox-rootfs}"
IMAGE_NAME="tracecat-sandbox-rootfs:latest"

echo "=== Building sandbox rootfs ==="
echo "Image: ${IMAGE_NAME}"
echo "Rootfs path: ${ROOTFS_PATH}"

# Build the sandbox image
echo ""
echo "Building Docker image..."
docker build -t "${IMAGE_NAME}" "${SCRIPT_DIR}"

# Create rootfs directory
echo ""
echo "Creating rootfs directory..."
mkdir -p "${ROOTFS_PATH}"

# Clean existing rootfs to ensure no stale files from previous builds
# This is important for security - leftover files could cause inconsistent behavior
# Remove and recreate the entire directory to catch hidden files (e.g., .dockerenv)
if [ -d "${ROOTFS_PATH}" ]; then
    echo "Cleaning existing rootfs directory..."
    rm -rf "${ROOTFS_PATH:?}"
    mkdir -p "${ROOTFS_PATH}"
fi

# Create a temporary container and export its filesystem
echo ""
echo "Exporting container filesystem..."
CONTAINER_ID=$(docker create "${IMAGE_NAME}")
trap "docker rm -f ${CONTAINER_ID} >/dev/null 2>&1 || true" EXIT

# Export the container filesystem
docker export "${CONTAINER_ID}" | tar -x -C "${ROOTFS_PATH}"

# Create additional directories that may be needed
mkdir -p "${ROOTFS_PATH}/tmp"
mkdir -p "${ROOTFS_PATH}/proc"
mkdir -p "${ROOTFS_PATH}/dev"

echo ""
echo "=== Sandbox rootfs built successfully ==="
echo "Location: ${ROOTFS_PATH}"
echo ""
echo "Rootfs contents:"
ls -la "${ROOTFS_PATH}"
