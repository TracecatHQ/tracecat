#!/usr/bin/env bash
set -euo pipefail

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
