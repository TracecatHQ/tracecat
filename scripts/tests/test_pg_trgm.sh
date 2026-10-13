#!/usr/bin/env bash
# Three isolated scenarios on an explicitly selected disposable PostgreSQL server.
# Requires Docker and Bash; never starts services or removes data volumes.
set -euo pipefail

: "${PGTRGM_TEST_CONTAINER:?Set PGTRGM_TEST_CONTAINER to a disposable PostgreSQL container}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
sql_file="$repo_root/scripts/postgres/pg-trgm.sql"
container=$PGTRGM_TEST_CONTAINER
admin_user=${PGTRGM_TEST_ADMIN:-postgres}
test_id="pgtrgm_$(date +%s)_$$"
reader="${test_id}_reader"
databases=("${test_id}_fresh" "${test_id}_existing" "${test_id}_invalid")
failure_log=$(mktemp)

admin() {
    local database=$1
    shift
    docker exec -i "$container" psql -X -U "$admin_user" -d "$database" \
        -v ON_ERROR_STOP=1 -v VERBOSITY=verbose "$@"
}

read_only_check() {
    docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' "$container" \
        psql -X -U "$reader" -d "$1" -v VERBOSITY=verbose
}

expect_failure() {
    local sqlstate=$1 message=$2
    shift 2
    if "$@" > "$failure_log" 2>&1; then
        echo "Expected failure: $message" >&2
        exit 1
    fi
    local failure_output
    failure_output=$(< "$failure_log")
    if [[ "$failure_output" != *"$sqlstate"* || "$failure_output" != *"$message"* ]]; then
        cat "$failure_log" >&2
        exit 1
    fi
}

cleanup() {
    local database
    for database in "${databases[@]}"; do
        admin postgres -c "DROP DATABASE IF EXISTS $database" >/dev/null 2>&1 || true
    done
    admin postgres -c "DROP ROLE IF EXISTS $reader" >/dev/null 2>&1 || true
    rm -f "$failure_log"
}
trap cleanup EXIT

admin postgres -c "CREATE ROLE $reader LOGIN" >/dev/null
for database in "${databases[@]}"; do
    admin postgres -c "CREATE DATABASE $database TEMPLATE template0" >/dev/null
    admin "$database" -c "GRANT USAGE, CREATE ON SCHEMA public TO $reader" >/dev/null
done

# 1. Fresh database: provisioning is explicit and repeatable; the app role
# checks in a read-only session and can create/use the required GIN index.
fresh=${databases[0]}
expect_failure 55000 'pg_trgm is not enabled' read_only_check "$fresh" < "$sql_file"
expect_failure 42501 'Cannot install pg_trgm with the current role' \
    docker exec -i "$container" psql -X -U "$reader" -d "$fresh" \
        -v VERBOSITY=verbose -v install=true < "$sql_file"
admin "$fresh" -v install=true < "$sql_file"
original_extension=$(admin "$fresh" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'pg_trgm'")
admin "$fresh" -v install=true < "$sql_file"
[[ $(admin "$fresh" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'pg_trgm'") == "$original_extension" ]]
read_only_check "$fresh" < "$sql_file"
docker exec -i "$container" psql -X -U "$reader" -d "$fresh" -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE synthetic_rows (body text);
INSERT INTO synthetic_rows VALUES ('synthetic fragment match'), ('unrelated text');
CREATE INDEX synthetic_rows_trgm_idx ON synthetic_rows USING gin (body public.gin_trgm_ops);
SET enable_seqscan = off;
DO $$
DECLARE
    query_plan jsonb;
BEGIN
    IF (SELECT count(*) FROM synthetic_rows WHERE body ILIKE '%fragment%') <> 1 THEN
        RAISE EXCEPTION 'GIN fragment matching returned the wrong rows';
    END IF;
    EXECUTE 'EXPLAIN (FORMAT JSON) SELECT count(*) FROM synthetic_rows WHERE body ILIKE ''%fragment%'''
        INTO query_plan;
    IF NOT jsonb_path_exists(
        query_plan, '$.** ? (@."Index Name" == "synthetic_rows_trgm_idx")'
    ) THEN
        RAISE EXCEPTION 'GIN fragment matching did not use synthetic_rows_trgm_idx';
    END IF;
END
$$;
SQL
echo 'PASS: fresh database, explicit installation, idempotence, read-only check and GIN use'

# 2. Existing application database: preserve pgvector and source data, and
# report the permissions the normal application/migration role actually lacks.
existing=${databases[1]}
admin "$existing" <<'SQL'
CREATE EXTENSION vector WITH SCHEMA public;
CREATE TABLE synthetic_source (embedding public.vector(3));
INSERT INTO synthetic_source VALUES ('[1,0,0]');
SQL
original_vector=$(admin "$existing" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'vector'")
admin "$existing" -v install=true < "$sql_file"
read_only_check "$existing" < "$sql_file"
[[ $(admin "$existing" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'vector'") == "$original_vector" ]]
[[ $(admin "$existing" -Atc 'SELECT embedding::text FROM synthetic_source') == '[1,0,0]' ]]
admin "$existing" -c "REVOKE USAGE ON SCHEMA public FROM PUBLIC, $reader" >/dev/null
expect_failure 42501 'needs USAGE on the public schema' read_only_check "$existing" < "$sql_file"
admin "$existing" -c "GRANT USAGE ON SCHEMA public TO $reader" >/dev/null
admin "$existing" -c 'REVOKE EXECUTE ON FUNCTION public.similarity(text,text) FROM PUBLIC' >/dev/null
expect_failure 42501 'needs EXECUTE on public.similarity' read_only_check "$existing" < "$sql_file"
echo 'PASS: existing data and pgvector preserved; schema/function permission diagnostics'

# 3. An existing extension in the wrong schema is never relocated. Objects
# detached from the extension must fail validation despite retaining their names.
invalid=${databases[2]}
admin "$invalid" -c 'CREATE SCHEMA synthetic_extensions; CREATE EXTENSION pg_trgm WITH SCHEMA synthetic_extensions' >/dev/null
original_extension=$(admin "$invalid" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'pg_trgm'")
expect_failure 55000 'pg_trgm must be installed in the public schema' \
    admin "$invalid" -v install=true < "$sql_file"
[[ $(admin "$invalid" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'pg_trgm'") == "$original_extension" ]]
[[ $(admin "$invalid" -Atc "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm'") == synthetic_extensions ]]
admin "$invalid" -c 'ALTER EXTENSION pg_trgm SET SCHEMA public; ALTER EXTENSION pg_trgm DROP OPERATOR CLASS public.gin_trgm_ops USING gin' >/dev/null
expect_failure 55000 'pg_trgm must provide public.gin_trgm_ops' read_only_check "$invalid" < "$sql_file"
admin "$invalid" -c 'ALTER EXTENSION pg_trgm ADD OPERATOR CLASS public.gin_trgm_ops USING gin; ALTER EXTENSION pg_trgm DROP FUNCTION public.similarity(text,text)' >/dev/null
expect_failure 55000 'pg_trgm must provide public.similarity' read_only_check "$invalid" < "$sql_file"
echo 'PASS: incorrect schema and unrelated same-named extension objects rejected'
