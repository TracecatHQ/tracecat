#!/usr/bin/env bash
# Enable the packaged pgvector extension before PostgreSQL accepts TCP connections.
set -euo pipefail

# Reuse the image's environment, permissions, and socket-only bootstrap helpers.
source /usr/local/bin/docker-entrypoint.sh
if [[ "${1:-}" == -* ]]; then
    set -- postgres "$@"
fi
# Preserve diagnostic commands, including postgres --help/--version.
if [[ "${1:-}" != postgres ]] || _pg_want_help "$@"; then
    exec /usr/local/bin/docker-entrypoint.sh "$@"
fi

if [[ ${PG_MAJOR:-} != 16 || ! -f /etc/debian_version ]]; then
    echo 'Automatic pgvector setup supports official Debian PostgreSQL 16 images only.' >&2
    exit 1
fi
# The upstream helpers intentionally permit unset optional environment values.
set +u
docker_setup_env
docker_create_db_directories
if [[ $(id -u) == 0 ]]; then
    exec gosu postgres bash "$BASH_SOURCE" "$@"
fi

if [[ -n "$DATABASE_ALREADY_EXISTS" ]]; then
    # Existing volumes skip initdb.d. Provision locally before opening TCP, just
    # as the official entrypoint does for a fresh volume's initialization scripts.
    temporary_server_started=false
    stop_temporary_server() {
        if [[ "$temporary_server_started" == true ]]; then
            docker_temp_server_stop
        fi
    }
    trap stop_temporary_server EXIT
    trap 'exit 143' TERM
    trap 'exit 130' INT
    export PGPASSWORD="${PGPASSWORD:-$POSTGRES_PASSWORD}"
    docker_temp_server_start "$@"
    temporary_server_started=true
    docker_process_sql -v install=true -f /usr/local/share/tracecat/pgvector.sql
    docker_temp_server_stop
    temporary_server_started=false
    trap - EXIT TERM INT
    unset PGPASSWORD
fi

exec /usr/local/bin/docker-entrypoint.sh "$@"
