#!/usr/bin/env bash
# Four isolated scenarios on an explicitly selected disposable PostgreSQL server.
# Requires Docker, Bash, uv and synced project dependencies; never removes volumes.
set -euo pipefail

: "${PGTRGM_TEST_CONTAINER:?Set PGTRGM_TEST_CONTAINER to a disposable PostgreSQL container}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
sql_file="$repo_root/scripts/postgres/pg-trgm.sql"
container=$PGTRGM_TEST_CONTAINER
admin_user=${PGTRGM_TEST_ADMIN:-postgres}
test_id="pgtrgm_$(date +%s)_$$"
reader="${test_id}_reader"
databases=("${test_id}_fresh" "${test_id}_existing" "${test_id}_invalid" "${test_id}_ibm")
failure_log=$(mktemp)
upgrade_sql=$(mktemp)
downgrade_sql=$(mktemp)

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

migrate() {
    local database=$1 user=${2:-$admin_user} sql=${3:-$upgrade_sql}
    docker exec -i "$container" psql -X -U "$user" -d "$database" \
        -v ON_ERROR_STOP=1 -v VERBOSITY=verbose < "$sql"
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
    rm -f "$failure_log" "$upgrade_sql" "$downgrade_sql"
}
trap cleanup EXIT

# Execute the revision through Alembic's real operations/offline SQL context.
# The resulting transactions are exercised against the selected live database.
(
    cd "$repo_root"
    uv run --no-sync python - "$upgrade_sql" "$downgrade_sql" <<'PY'
import sys

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("3e54217aadda")
if revision is None:
    raise RuntimeError("Trigram migration is missing")
migration = revision.module
for path, operation in ((sys.argv[1], migration.upgrade), (sys.argv[2], migration.downgrade)):
    with open(path, "w") as output:
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "transactional_ddl": True, "output_buffer": output},
        )
        with Operations.context(context), context.begin_transaction():
            operation()
PY
)

admin postgres -c "CREATE ROLE $reader LOGIN" >/dev/null
for database in "${databases[@]}"; do
    admin postgres -c "CREATE DATABASE $database TEMPLATE template0" >/dev/null
    admin "$database" -c "GRANT USAGE, CREATE ON SCHEMA public TO $reader" >/dev/null
done

# 1. Fresh database: migrate with a non-superuser trusted-extension installer,
# then rerun with installation privileges removed. Downgrade preserves GIN use.
fresh=${databases[0]}
expect_failure 55000 'pg_trgm is not enabled' read_only_check "$fresh" < "$sql_file"
expect_failure 42501 'Cannot install pg_trgm with the current role' migrate "$fresh" "$reader"
admin "$fresh" -c "GRANT CREATE ON DATABASE $fresh TO $reader" >/dev/null
migrate "$fresh" "$reader"
original_extension=$(admin "$fresh" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'pg_trgm'")
admin "$fresh" -c "REVOKE CREATE ON DATABASE $fresh FROM $reader" >/dev/null
migrate "$fresh" "$reader"
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
migrate "$fresh" "$reader" "$downgrade_sql"
[[ $(admin "$fresh" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'pg_trgm'") == "$original_extension" ]]
[[ $(admin "$fresh" -Atc "SELECT count(*) FROM synthetic_rows WHERE body ILIKE '%fragment%'") == 1 ]]
echo 'PASS: trusted-role migration, restricted-role rerun, read-only check, GIN use and retained downgrade'

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
migrate "$existing" "$reader"
read_only_check "$existing" < "$sql_file"
[[ $(admin "$existing" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'vector'") == "$original_vector" ]]
[[ $(admin "$existing" -Atc 'SELECT embedding::text FROM synthetic_source') == '[1,0,0]' ]]
admin "$existing" -c "REVOKE USAGE ON SCHEMA public FROM PUBLIC, $reader" >/dev/null
expect_failure 42501 'needs USAGE on the public schema' read_only_check "$existing" < "$sql_file"
expect_failure 42501 'needs USAGE on the public schema' migrate "$existing" "$reader"
admin "$existing" -c "GRANT USAGE ON SCHEMA public TO $reader" >/dev/null
admin "$existing" -c 'REVOKE EXECUTE ON FUNCTION public.similarity(text,text) FROM PUBLIC' >/dev/null
expect_failure 42501 'needs EXECUTE on public.similarity' read_only_check "$existing" < "$sql_file"
[[ $(< "$failure_log") == *"GRANT EXECUTE ON FUNCTION public.similarity(text,text) TO $reader;"* ]]
expect_failure 42501 'needs EXECUTE on public.similarity' migrate "$existing" "$reader"
admin "$existing" -c "GRANT EXECUTE ON FUNCTION public.similarity(text,text) TO $reader" >/dev/null
read_only_check "$existing" < "$sql_file"
admin "$existing" -c "REVOKE EXECUTE ON FUNCTION public.strict_word_similarity(text,text) FROM PUBLIC, $reader" >/dev/null
expect_failure 42501 'needs EXECUTE on public.strict_word_similarity' read_only_check "$existing" < "$sql_file"
[[ $(< "$failure_log") == *"GRANT EXECUTE ON FUNCTION public.strict_word_similarity(text,text) TO $reader;"* ]]
admin "$existing" -c "GRANT EXECUTE ON FUNCTION public.strict_word_similarity(text,text) TO $reader" >/dev/null
read_only_check "$existing" < "$sql_file"
operator_function=$(admin "$existing" -Atc "SELECT format('%I.%I(text,text)', n.nspname, p.proname) FROM pg_operator o JOIN pg_proc p ON p.oid = o.oprcode JOIN pg_namespace n ON n.oid = p.pronamespace WHERE o.oid = 'public.%>>(text,text)'::regoperator")
[[ -n "$operator_function" ]]
admin "$existing" -c "REVOKE EXECUTE ON FUNCTION $operator_function FROM PUBLIC, $reader" >/dev/null
expect_failure 42501 "needs EXECUTE on $operator_function for public.%>>" read_only_check "$existing" < "$sql_file"
[[ $(< "$failure_log") == *"GRANT EXECUTE ON FUNCTION $operator_function TO $reader;"* ]]
admin "$existing" -c "GRANT EXECUTE ON FUNCTION $operator_function TO $reader" >/dev/null
read_only_check "$existing" < "$sql_file"
[[ $(admin "$existing" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'vector'") == "$original_vector" ]]
[[ $(admin "$existing" -Atc 'SELECT embedding::text FROM synthetic_source') == '[1,0,0]' ]]
echo 'PASS: existing data and pgvector preserved; missing and granted schema/function/operator permissions'

# 3. An existing extension outside public is used in place, never relocated. Objects
# detached from the extension must fail validation despite retaining their names.
invalid=${databases[2]}
admin "$invalid" -c 'CREATE SCHEMA synthetic_extensions; CREATE EXTENSION pg_trgm WITH SCHEMA synthetic_extensions' >/dev/null
original_extension=$(admin "$invalid" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'pg_trgm'")
migrate "$invalid"
[[ $(admin "$invalid" -Atc "SELECT oid || ':' || extversion FROM pg_extension WHERE extname = 'pg_trgm'") == "$original_extension" ]]
[[ $(admin "$invalid" -Atc "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm'") == synthetic_extensions ]]
admin "$invalid" -c 'ALTER EXTENSION pg_trgm SET SCHEMA public; ALTER EXTENSION pg_trgm DROP OPERATOR CLASS public.gin_trgm_ops USING gin' >/dev/null
expect_failure 55000 'pg_trgm must provide public.gin_trgm_ops' read_only_check "$invalid" < "$sql_file"
expect_failure 55000 'pg_trgm must provide public.gin_trgm_ops' migrate "$invalid"
admin "$invalid" -c 'ALTER EXTENSION pg_trgm ADD OPERATOR CLASS public.gin_trgm_ops USING gin; ALTER EXTENSION pg_trgm DROP FUNCTION public.similarity(text,text)' >/dev/null
expect_failure 55000 'pg_trgm must provide public.similarity' read_only_check "$invalid" < "$sql_file"
expect_failure 55000 'pg_trgm must provide public.similarity' migrate "$invalid"
admin "$invalid" -c 'ALTER EXTENSION pg_trgm ADD FUNCTION public.similarity(text,text); ALTER EXTENSION pg_trgm DROP FUNCTION public.strict_word_similarity(text,text)' >/dev/null
expect_failure 55000 'pg_trgm must provide public.strict_word_similarity' read_only_check "$invalid" < "$sql_file"
admin "$invalid" -c 'ALTER EXTENSION pg_trgm ADD FUNCTION public.strict_word_similarity(text,text); ALTER EXTENSION pg_trgm DROP OPERATOR public.%>>(text,text)' >/dev/null
expect_failure 55000 'pg_trgm must provide public.%>>(text,text)' read_only_check "$invalid" < "$sql_file"
admin "$invalid" -c "ALTER EXTENSION pg_trgm ADD OPERATOR public.%>>(text,text); ALTER EXTENSION pg_trgm DROP FUNCTION $operator_function" >/dev/null
expect_failure 55000 'pg_trgm must own the implementation of public.%>>(text,text)' read_only_check "$invalid" < "$sql_file"
admin "$invalid" -c "ALTER EXTENSION pg_trgm ADD FUNCTION $operator_function" >/dev/null
read_only_check "$invalid" < "$sql_file"
echo 'PASS: existing schema kept and unrelated same-named extension objects rejected'

# 4. IBM Cloud Databases for PostgreSQL: a fresh install goes into the
# provider-managed ibm_extension schema, which the migration role cannot write.
ibm=${databases[3]}
admin "$ibm" -c "CREATE SCHEMA ibm_extension; GRANT USAGE ON SCHEMA ibm_extension TO $reader; GRANT CREATE ON DATABASE $ibm TO $reader" >/dev/null
# IBM redirects plain CREATE EXTENSION into ibm_extension; listing it first emulates that.
admin "$ibm" -c "ALTER DATABASE $ibm SET search_path = ibm_extension, \"\$user\", public" >/dev/null
migrate "$ibm" "$reader"
[[ $(admin "$ibm" -Atc "SELECT extnamespace::regnamespace FROM pg_extension WHERE extname = 'pg_trgm'") == ibm_extension ]]
[[ $(admin "$ibm" -Atc "SELECT ibm_extension.similarity('synthetic search', 'synthetic search')") == 1 ]]
echo 'PASS: fresh install uses the read-only ibm_extension schema when it exists'
