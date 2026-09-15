#!/usr/bin/env bash
# Compose POCs: add pgvector without changing PostgreSQL or its runtime packages.
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
lib_dir=$(pg_config --pkglibdir)
ext_dir="$(pg_config --sharedir)/extension"

# A pre-provisioned image needs no download. SQL provisioning checks its version.
if [[ ! -f "$lib_dir/vector.so" || ! -f "$ext_dir/vector.control" ]]; then
    if [[ $(id -u) != 0 ]]; then
        echo 'Automatic pgvector setup needs the official image default root entrypoint.' >&2
        exit 1
    fi
    # A new base/runtime gets a separate cache; never mix Debian or CPU builds.
    cache_key=$({
        cat /etc/os-release
        dpkg --print-architecture
        pg_config --version
        getconf GNU_LIBC_VERSION
        sha256sum "$(command -v postgres)"
    } | sha256sum | cut -d ' ' -f 1)
    cache_root=/var/cache/tracecat-pgvector
    cache="$cache_root/0.8.6-$cache_key"
    mkdir -p "$cache_root"
    # Serialize containers using the same cache volume during a recreation.
    exec 9> "$cache_root/install.lock"
    flock 9
    if [[ ! -f "$cache/SHA256SUMS" ]] || ! (cd "$cache" && sha256sum --status -c SHA256SUMS); then
        staging=$(mktemp -d "$cache_root/download.XXXXXX")
        trap 'rm -rf "$staging"' EXIT
        echo 'Downloading pgvector 0.8.6 from the PostgreSQL image package repository...'
        # apt verifies signed repository metadata and package hashes. Download
        # and extract only: never apt install/upgrade PostgreSQL, libc or ICU.
        apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 update -qq
        package="postgresql-$PG_MAJOR-pgvector"
        version=$(apt-cache madison "$package" | awk '$3 ~ /^0\.8\.6-/ && !found {print $3; found=1}')
        if [[ -z "$version" ]]; then
            echo "The configured package repository has no pgvector 0.8.6 build for this image." >&2
            exit 1
        fi
        (cd "$staging" && apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 download "$package=$version")
        dpkg-deb --extract "$staging/"*.deb "$staging/unpacked"
        mkdir "$staging/bundle"
        cp "$staging/unpacked$lib_dir/vector.so" "$staging/bundle/"
        cp "$staging/unpacked$ext_dir/"vector.control "$staging/bundle/"
        cp "$staging/unpacked$ext_dir/"vector--*.sql "$staging/bundle/"
        (cd "$staging/bundle" && sha256sum vector.so vector.control vector--*.sql > SHA256SUMS)
        rm -rf "$cache"
        mv "$staging/bundle" "$cache"
        rm -rf "$staging"
        trap - EXIT
    else
        echo 'Using cached pgvector 0.8.6 package.'
    fi
    dependencies=$(ldd "$cache/vector.so")
    if [[ "$dependencies" == *'not found'* ]]; then
        echo 'The pgvector binary requires libraries absent from this PostgreSQL image.' >&2
        exit 1
    fi
    install -m 755 "$cache/vector.so" "$lib_dir/vector.so"
    install -m 644 "$cache/vector.control" "$cache/"vector--*.sql "$ext_dir/"
    flock -u 9
    exec 9>&-
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
