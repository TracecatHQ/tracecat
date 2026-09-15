#!/usr/bin/env bash
# Isolated image/extension regression test. Requires only Docker and Bash.
# Retain the synthetic volume for inspection; never remove existing data volumes.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
sql_file="$repo_root/scripts/postgres/pgvector.sql"
# postgres:16 resolved to Trixie before this change. Pin that source version so
# the regression remains reproducible after the floating tag advances.
source_image=${1:-postgres:16.14-trixie}
expect_mismatch=${2:-}
test_id="tracecat-pgvector-test-$(date +%s)-$$"
volume="$test_id-data"
container="$test_id"
failure_log=$(mktemp)
vector_image=${PGVECTOR_TEST_IMAGE:-$test_id-image}
if [[ -z "${PGVECTOR_TEST_IMAGE:-}" ]]; then
    # Pull only for this synthetic test; production builds inspect the running
    # container and never resolve a floating tag.
    docker image inspect "$source_image" >/dev/null 2>&1 || docker pull "$source_image"
    bash "$repo_root/scripts/postgres/build-pgvector-image.sh" --image "$source_image" "$vector_image"
fi

cleanup() {
    docker rm -f "$container" >/dev/null 2>&1 || true
    rm -f "$failure_log"
    echo "Synthetic test data retained in Docker volume: $volume"
}
trap cleanup EXIT

start_database() {
    # Bypass automatic bootstrap here to test SQL permissions independently.
    # The startup test exercises the packaged entrypoint on fresh/existing volumes.
    docker run -d --name "$container" --entrypoint docker-entrypoint.sh \
        -e POSTGRES_PASSWORD=synthetic-test-password \
        -v "$volume:/var/lib/postgresql/data" "$1" postgres >/dev/null
    for ((attempt = 0; attempt < 60; attempt++)); do
        # TCP waits for the final server, not the temporary initdb server.
        if docker exec "$container" pg_isready -h 127.0.0.1 -U postgres -d postgres >/dev/null; then
            return
        fi
        sleep 1
    done
    docker logs "$container"
    echo "PostgreSQL did not become ready" >&2
    exit 1
}

expect_failure() {
    local expected=$1
    shift
    if "$@" < "$sql_file" > "$failure_log" 2>&1; then
        echo "Expected failure: $expected" >&2
        exit 1
    fi
    if ! grep -Fq "$expected" "$failure_log"; then
        cat "$failure_log" >&2
        exit 1
    fi
}

# Verify the server package inventory and core runtime binaries are unchanged.
# Skip this comparison only for the deliberate cross-distribution negative test.
runtime_manifest() {
    docker run --rm --entrypoint sh "$1" -c '
        postgres --version
        dpkg-query -W
        sha256sum "$(command -v postgres)"
        find /usr/lib /lib -type f \( -name "libicu*.so.*" -o -name "libc.so.6" \) -exec sha256sum {} + | sort
    '
}
if [[ "$expect_mismatch" != '--expect-collation-mismatch' ]]; then
    [[ "$(runtime_manifest "$source_image")" == "$(runtime_manifest "$vector_image")" ]]
    echo 'PASS: PostgreSQL, libc, ICU and installed package versions unchanged'
fi

# Start with the previous plain PostgreSQL image and write durable source data.
start_database "$source_image"
docker exec -i "$container" psql -X -U postgres -d postgres -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE source_rows (id integer PRIMARY KEY, body text NOT NULL);
INSERT INTO source_rows VALUES (1, 'Synthetic source text survives the image upgrade');
CREATE UNIQUE INDEX source_rows_body_idx ON source_rows (body);
CREATE ROLE vector_test_reader LOGIN;
GRANT USAGE, CREATE ON SCHEMA public TO vector_test_reader;
GRANT SELECT ON source_rows TO vector_test_reader;
SQL
expect_failure 'pgvector is not available' \
    docker exec -i "$container" psql -X -U postgres -d postgres -v install=true
docker stop "$container" >/dev/null
docker rm "$container" >/dev/null

# Reuse the exact same PostgreSQL 16 data volume with the pinned pgvector image.
start_database "$vector_image"
if [[ "$expect_mismatch" == '--expect-collation-mismatch' ]]; then
    expect_failure 'PostgreSQL collation version mismatch' \
        docker exec -i "$container" psql -X -U postgres -d postgres -v install=true
    expect_failure 'PostgreSQL collation version mismatch' \
        docker exec -i "$container" psql -X -U vector_test_reader -d postgres
    # The guard must fail before installation, not leave a partial extension.
    installed=$(docker exec "$container" psql -X -U postgres -d postgres -At \
        -c "SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
    [[ "$installed" == 0 ]]
    echo 'PASS: incompatible collation libraries rejected before provisioning'
    exit 0
fi
expect_failure 'pgvector is not enabled' \
    docker exec -i "$container" psql -X -U vector_test_reader -d postgres
expect_failure 'permission denied to create extension' \
    docker exec -i "$container" psql -X -U vector_test_reader -d postgres -v install=true

docker exec -i "$container" psql -X -U postgres -d postgres -v install=true < "$sql_file"
# Installation is idempotent, and checking needs no privileged role or writes.
docker exec -i "$container" psql -X -U postgres -d postgres -v install=true < "$sql_file"
docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' "$container" \
    psql -X -U vector_test_reader -d postgres < "$sql_file"

docker exec -i "$container" psql -X -U vector_test_reader -d postgres -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
    IF (SELECT body FROM source_rows WHERE id = 1)
        IS DISTINCT FROM 'Synthetic source text survives the image upgrade' THEN
        RAISE EXCEPTION 'Source data changed during the image upgrade';
    END IF;
END
$$;
CREATE TABLE vector_test_rows (id integer PRIMARY KEY, embedding public.vector(3));
INSERT INTO vector_test_rows VALUES (1, '[1,0,0]'), (2, '[0,1,0]');
DO $$
BEGIN
    IF (SELECT id FROM vector_test_rows ORDER BY embedding <=> '[1,0,0]' LIMIT 1) <> 1 THEN
        RAISE EXCEPTION 'Cosine ranking returned the wrong row';
    END IF;
    IF (SELECT embedding <=> '[0,1,0]' FROM vector_test_rows WHERE id = 1) <> 1 THEN
        RAISE EXCEPTION 'Unexpected cosine distance';
    END IF;
END
$$;
SQL

# Extension installation is database-local, not a server-wide side effect.
docker exec "$container" psql -X -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c 'CREATE DATABASE separate_database'
expect_failure 'pgvector is not enabled' \
    docker exec -i "$container" psql -X -U postgres -d separate_database

echo "PASS: PostgreSQL 16 data preserved; vector provisioning, permissions and cosine search verified"
