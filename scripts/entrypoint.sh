#!/usr/bin/env bash
set -euo pipefail

# Define absolute paths for binaries to avoid PATH manipulation security issues
PODMAN_BIN="/usr/bin/podman"
PYTHON_BIN="/usr/bin/python3"
CRUN_BIN="/usr/bin/crun"
FUSE_OVERLAYFS_BIN="/usr/bin/fuse-overlayfs"

# Function to run migrations
run_migrations() {
    echo "Running database migrations..."
    if ! ${PYTHON_BIN} -m alembic upgrade head; then
        echo "ERROR: Migration failed!"
        return 1
    fi
    echo "Migrations completed successfully."
}

# Function to validate binary exists and has proper permissions
validate_binary() {
    local binary_path="$1"
    if [[ ! -x "${binary_path}" ]]; then
        echo "ERROR: Required binary not found or not executable: ${binary_path}"
        return 1
    fi
    # Verify binary ownership/permissions
    if [[ "$(stat -c '%U' ${binary_path})" != "root" ]]; then
        echo "WARNING: Binary ${binary_path} is not owned by root. This is a potential security risk."
    fi
    return 0
}

# Function to setup Podman for secure nested container execution
setup_podman() {
    echo "Setting up Podman for nested container execution..."

    # Validate required binaries exist
    if ! validate_binary "${PODMAN_BIN}"; then
        echo "WARNING: Podman not found or not executable, skipping Podman setup"
        return 0
    fi

    # Check for crun and fuse-overlayfs without failing if they're missing
    validate_binary "${CRUN_BIN}" || true
    validate_binary "${FUSE_OVERLAYFS_BIN}" || true

    # Create socket directory with proper permissions
    SOCKET_DIR="/run/podman"
    if [[ ! -d "${SOCKET_DIR}" ]]; then
        mkdir -p "${SOCKET_DIR}"
        chmod 750 "${SOCKET_DIR}"  # More secure permissions
        echo "Created Podman socket directory with secure permissions (750)"
    else
        # Check socket directory permissions
        SOCKET_PERMS=$(stat -c '%a' "${SOCKET_DIR}")
        if [[ "${SOCKET_PERMS}" != "750" && "${SOCKET_PERMS}" != "700" ]]; then
            echo "WARNING: Podman socket directory has insecure permissions: ${SOCKET_PERMS}"
            echo "Fixing permissions to 750..."
            chmod 750 "${SOCKET_DIR}"
        fi
    fi

    # Configure containers.conf with security hardening
    CONTAINERS_CONF="/etc/containers/containers.conf"
    if [[ ! -f "${CONTAINERS_CONF}" ]]; then
        mkdir -p /etc/containers
        cat > "${CONTAINERS_CONF}" << EOL
[engine]
runtime = "crun"
# Default to rootless mode
rootless = true
# Drop all capabilities by default for security
default_capabilities = []
# Default network isolation
default_network = "none"

[engine.runtimes]
crun = [
    "${CRUN_BIN}"
]

[containers]
# Security options
default_security_opt = ["no-new-privileges:true", "seccomp=default"]
EOL
        chmod 644 "${CONTAINERS_CONF}"
    fi

    # Set up storage.conf for overlay driver
    STORAGE_CONF="/etc/containers/storage.conf"
    if [[ ! -f "${STORAGE_CONF}" ]] && [[ -x "${FUSE_OVERLAYFS_BIN}" ]]; then
        mkdir -p /etc/containers
        cat > "${STORAGE_CONF}" << EOL
[storage]
driver = "overlay"

[storage.options]
mount_program = "${FUSE_OVERLAYFS_BIN}"
# Use user namespace remapping for additional isolation
userns_remap = "apiuser:apiuser"
EOL
        chmod 644 "${STORAGE_CONF}"
    fi

    # Define user-specific podman configuration for non-root users
    if [[ $(id -u) -ne 0 && -d "$HOME" ]]; then
        USER_CONTAINERS_DIR="$HOME/.config/containers"
        mkdir -p "${USER_CONTAINERS_DIR}"

        # User-specific containers.conf
        USER_CONTAINERS_CONF="${USER_CONTAINERS_DIR}/containers.conf"
        if [[ ! -f "${USER_CONTAINERS_CONF}" ]]; then
            cat > "${USER_CONTAINERS_CONF}" << EOL
[engine]
rootless = true

[containers]
default_security_opt = ["no-new-privileges:true", "seccomp=default"]
EOL
            chmod 600 "${USER_CONTAINERS_CONF}"
        fi
    fi

    # Test that podman works with absolute path and proper error capture
    echo "Verifying Podman installation..."
    if "${PODMAN_BIN}" version &> /dev/null; then
        echo "Podman version check passed."

        # More thorough test - try to run a simple container if in production
        if [[ -n "${TRACECAT_ENVIRONMENT:-}" && "${TRACECAT_ENVIRONMENT}" == "production" ]]; then
            if "${PODMAN_BIN}" run --rm --rootless --network=none alpine:latest echo "Podman test" &> /dev/null; then
                echo "Podman container test passed."
            else
                echo "WARNING: Podman container test failed. Nested containers may not work properly."
            fi
        fi
    else
        echo "ERROR: Podman is not working correctly. Container operations will fail."
    fi
}

# Check if we need to run migrations (only for API)
if [[ "${RUN_MIGRATIONS:-false}" == "true" ]]; then
    if ! run_migrations; then
        echo "Exiting due to migration failure"
        exit 1
    fi
fi

# Setup Podman for nested containers
setup_podman

# Log the command we're about to execute
echo "Executing command: $@"

# Execute the CMD with proper exec to replace shell process
exec "$@"
