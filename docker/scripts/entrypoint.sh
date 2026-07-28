#!/usr/bin/env bash
set -euo pipefail

# When started as root with delegation requested (the sandbox Compose overlay
# pairs TRACECAT__ENTRYPOINT_CGROUP_DELEGATE with user: "0:0"), hand the
# container's cgroup v2 subtree to apiuser so agent sandbox memory limits can
# be prepared without root, then drop privileges before doing anything else.
if [[ "$(id -u)" == "0" && "${TRACECAT__ENTRYPOINT_CGROUP_DELEGATE:-false}" == "true" ]]; then
    if mount -o remount,rw /sys/fs/cgroup &&
        chown apiuser:apiuser /sys/fs/cgroup /sys/fs/cgroup/cgroup.procs \
            /sys/fs/cgroup/cgroup.subtree_control /sys/fs/cgroup/cgroup.threads; then
        echo "Delegated /sys/fs/cgroup to apiuser."
    else
        echo "Unable to delegate /sys/fs/cgroup to apiuser; agent sandbox" \
            "cgroup limits will be unavailable." >&2
    fi
    # setpriv changes only IDs; fix the identity env vars ourselves instead of
    # --reset-env, which would clear the service configuration environment.
    export HOME=/home/apiuser USER=apiuser LOGNAME=apiuser
    exec setpriv --reuid=apiuser --regid=apiuser --init-groups /app/entrypoint.sh "$@"
fi

# Function to run migrations
run_migrations() {
    echo "Running database migrations..."
    if ! python3 -m alembic upgrade head; then
        echo "Migration failed!"
        return 1
    fi
    echo "Migrations completed successfully."
}

# Check if we need to run migrations (only for API)
if [[ "${RUN_MIGRATIONS:-false}" == "true" ]]; then
    if ! run_migrations; then
        echo "Exiting due to migration failure"
        exit 1
    fi
fi

# Execute the CMD
exec "$@"
