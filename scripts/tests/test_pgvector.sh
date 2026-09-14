#!/usr/bin/env bash
# Isolated image/extension regression test. Requires only Docker and Bash.
# Retain the synthetic volume for inspection; never remove existing data volumes.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
sql_file="$repo_root/scripts/postgres/pgvector.sql"
vector_image=$(awk '/image: pgvector\/pgvector:/ {print $2}' "$repo_root/docker-compose.dev.yml")
test_id="tracecat-pgvector-test-$(date +%s)-$$"
volume="$test_id-data"
container="$test_id"
failure_log=$(mktemp)

cleanup() {
    docker rm -f "$container" >/dev/null 2>&1 || true
    rm -f "$failure_log"
    echo "Synthetic test data retained in Docker volume: $volume"
}
trap cleanup EXIT

start_database() {
    docker run -d --name "$container" \
        -e POSTGRES_PASSWORD=synthetic-test-password \
        -v "$volume:/var/lib/postgresql/data" "$1" >/dev/null
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

# Start with the previous plain PostgreSQL image and write durable source data.
start_database postgres:16.14-bookworm
docker exec -i "$container" psql -X -U postgres -d postgres -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE source_rows (id integer PRIMARY KEY, body text NOT NULL);
INSERT INTO source_rows VALUES (1, 'Synthetic source text survives the image upgrade');
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
