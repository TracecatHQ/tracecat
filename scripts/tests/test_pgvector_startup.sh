#!/usr/bin/env bash
# Real Compose startup using the production wiring and a small SQL migration probe.
# No image build, pgvector override, or manual extension installation.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work_dir=$(mktemp -d)
project="pgvector-startup-$(date +%s)-$$"
source_image=${1:-postgres:16}
scenario=${2:-existing}
admin_user=${3:-postgres}
unset COMPOSE_FILE TRACECAT__PGVECTOR_IMAGE
export COMPOSE_PROJECT_NAME="$project"
export TRACECAT__POSTGRES_USER="$admin_user" TRACECAT__POSTGRES_PASSWORD=synthetic-test-password
cp "$repo_root/docker-compose.yml" "$work_dir/compose.yaml"
mkdir -p "$work_dir/scripts/postgres"
cp "$repo_root/scripts/postgres/"{compose-entrypoint.sh,initdb-pgvector.sql,pgvector.sql} "$work_dir/scripts/postgres/"
cat > "$work_dir/.env" <<ENV
TRACECAT__LOCAL_REPOSITORY_PATH=/tmp/synthetic-registry
PUBLIC_APP_PORT=8080
TEMPORAL__UI_VERSION=latest
TRACECAT__DB_URI=postgresql+psycopg://$admin_user:synthetic-test-password@postgres_db:5432/postgres
ENV
cd "$work_dir"
cleanup() {
    result=$?
    if [[ $result != 0 ]]; then
        tail -8 "$work_dir/warnings.log" >&2 || true
        docker compose logs postgres_db migrations 2>/dev/null | tail -60 >&2 || true
    fi
    docker compose down >/dev/null 2>&1 || true
    docker rm -f "$project-seed" >/dev/null 2>&1 || true
    echo "Retained synthetic volumes for $project"
    rm -rf "$work_dir"
}
trap cleanup EXIT
docker compose config --format json > rendered.json 2> warnings.log
python3 - "$source_image" "$admin_user" <<'PY'
import json
import sys
from pathlib import Path
config = json.loads(Path('rendered.json').read_text())
config['services'] = {k: config['services'][k] for k in ('postgres_db', 'migrations')}
postgres = config['services']['postgres_db']
assert postgres['image'] == 'postgres:16', 'Default startup must need no image setting'
postgres['image'] = sys.argv[1]  # Test another supported distribution when requested.
postgres.pop('ports', None)
postgres.pop('container_name', None)
config['services']['migrations'] = {
    'image': sys.argv[1],
    'environment': {'PGHOST': 'postgres_db', 'PGUSER': sys.argv[2], 'PGPASSWORD': 'synthetic-test-password', 'PGDATABASE': 'postgres'},
    'entrypoint': ['psql', '-X', '-v', 'ON_ERROR_STOP=1', '-c',
        "CREATE TABLE IF NOT EXISTS synthetic_vectors (embedding vector(3)); INSERT INTO synthetic_vectors VALUES ('[1,0,0]');"],
    'depends_on': config['services']['migrations']['depends_on'],
    'networks': config['services']['migrations']['networks'],
}
config['volumes'] = {k: config['volumes'][k] for k in ('core-db', 'pgvector-cache')}
Path('compose.yaml').write_text(json.dumps(config))
PY
if [[ "$scenario" == existing ]]; then
    # Create a source row with the unmodified plain image first.
    docker run -d --name "$project-seed" -e POSTGRES_PASSWORD=synthetic-test-password -e POSTGRES_USER="$admin_user" -e POSTGRES_DB=postgres \
        -v "${project}_core-db:/var/lib/postgresql/data" "$source_image" >/dev/null
    for ((i=0;i<60;i++)); do
        if docker exec "$project-seed" pg_isready -h 127.0.0.1 -U "$admin_user" -d postgres >/dev/null 2>&1; then break; fi
        sleep 1
    done
    docker exec "$project-seed" psql -X -U "$admin_user" -d postgres -v ON_ERROR_STOP=1 \
        -c "CREATE TABLE synthetic_source (body text); INSERT INTO synthetic_source VALUES ('preserved');" >/dev/null
    docker stop "$project-seed" >/dev/null
    docker rm "$project-seed" >/dev/null
fi
# Hold initialization briefly to prove TCP health cannot pass before setup SQL.
{ printf '\\echo SYNTHETIC_BOOTSTRAP_CHECK\nSELECT pg_sleep(5);\n'; cat "$repo_root/scripts/postgres/pgvector.sql"; } > scripts/postgres/pgvector.sql
docker compose up -d migrations > /dev/null 2>> warnings.log &
startup_pid=$!
for ((i=0;i<180;i++)); do
    if docker compose logs postgres_db 2>> warnings.log | grep -F SYNTHETIC_BOOTSTRAP_CHECK > /dev/null; then break; fi
    sleep 1
done
# The temporary server accepts sockets only, even for existing-volume upgrades.
if docker compose exec -T postgres_db pg_isready -h 127.0.0.1 -U "$admin_user" -d postgres > /dev/null 2>> warnings.log; then
    echo 'PostgreSQL became TCP-ready before pgvector provisioning completed' >&2
    exit 1
fi
wait "$startup_pid"
cp "$repo_root/scripts/postgres/pgvector.sql" scripts/postgres/pgvector.sql
container=$(docker compose ps --all --quiet migrations 2>> warnings.log)
[[ -n "$container" && $(docker wait "$container") == 0 ]]
[[ $(docker compose exec -T postgres_db psql -X -U "$admin_user" -d postgres -At -c 'SELECT count(*) FROM synthetic_vectors' 2>> warnings.log) == 1 ]]
if [[ "$scenario" == existing ]]; then
    [[ $(docker compose exec -T postgres_db psql -X -U "$admin_user" -d postgres -At -c 'SELECT body FROM synthetic_source' 2>> warnings.log) == preserved ]]
fi
container=$(docker compose ps -q postgres_db 2>> warnings.log)
# Installing the extension files must not alter the base's installed packages.
before=$(docker run --rm --entrypoint bash "$source_image" -c 'dpkg-query -W; sha256sum "$(command -v postgres)"; find /usr/lib /lib -type f \( -name "libicu*.so.*" -o -name "libc.so.6" \) -exec sha256sum {} + | sort')
after=$(docker exec "$container" bash -c 'dpkg-query -W; sha256sum "$(command -v postgres)"; find /usr/lib /lib -type f \( -name "libicu*.so.*" -o -name "libc.so.6" \) -exec sha256sum {} + | sort')
[[ "$before" == "$after" ]]
# Recreating the container must restore extension files from cache and be idempotent.
docker compose up -d --force-recreate postgres_db > /dev/null 2>> warnings.log
docker compose up -d --force-recreate migrations > /dev/null 2>> warnings.log
container=$(docker compose ps --all --quiet migrations 2>> warnings.log)
[[ -n "$container" && $(docker wait "$container") == 0 ]]
docker compose logs postgres_db 2>> warnings.log | grep -F 'Using cached pgvector 0.8.6 package.' > /dev/null
[[ $(docker compose exec -T postgres_db psql -X -U "$admin_user" -d postgres -At -c 'SELECT count(*) FROM synthetic_vectors' 2>> warnings.log) == 2 ]]
# Rejected SQL must stop the temporary server and prevent migration startup.
docker compose rm -f migrations > /dev/null 2>> warnings.log
python3 - <<'PYFAILURE'
import json
from pathlib import Path
config = json.loads(Path('compose.yaml').read_text())
config['services']['postgres_db']['restart'] = 'no'
Path('compose.yaml').write_text(json.dumps(config))
PYFAILURE
docker compose exec -T postgres_db psql -X -U "$admin_user" -d postgres -v ON_ERROR_STOP=1 \
    -c 'CREATE SCHEMA synthetic_vector; ALTER EXTENSION vector SET SCHEMA synthetic_vector' > /dev/null 2>> warnings.log
if docker compose up -d --force-recreate postgres_db migrations > /dev/null 2>> warnings.log; then
    echo 'Expected failed provisioning to block migrations' >&2
    exit 1
fi
container=$(docker compose ps --all --quiet postgres_db 2>> warnings.log)
[[ $(docker inspect --format '{{.State.Running}}' "$container") == false ]]
docker compose logs postgres_db 2>> warnings.log | grep -F 'database system is shut down' > /dev/null
docker compose logs postgres_db 2>> warnings.log | grep -F 'pgvector must be installed in the public schema' > /dev/null
# Simulate an administrator correcting the schema during local bootstrap.
# The normal entrypoint never relocates extensions automatically.
{ printf 'ALTER EXTENSION vector SET SCHEMA public;\n'; cat "$repo_root/scripts/postgres/pgvector.sql"; } > scripts/postgres/repair-pgvector.sql
# Mount a new file so Docker Desktop cannot reuse cached metadata from the old
# bind mount when its contents change while the container is stopped.
python3 - <<'PYREPAIR'
import json
from pathlib import Path
config = json.loads(Path('compose.yaml').read_text())
for volume in config['services']['postgres_db']['volumes']:
    if volume['target'] == '/usr/local/share/tracecat/pgvector.sql':
        volume['source'] = str(Path('scripts/postgres/repair-pgvector.sql').resolve())
Path('compose.yaml').write_text(json.dumps(config))
PYREPAIR
docker compose up -d --force-recreate postgres_db > /dev/null 2>> warnings.log
for ((i=0;i<60;i++)); do
    if docker compose exec -T postgres_db pg_isready -h 127.0.0.1 -U "$admin_user" -d postgres > /dev/null 2>> warnings.log; then break; fi
    sleep 1
done
docker compose exec -T postgres_db pg_isready -h 127.0.0.1 -U "$admin_user" -d postgres > /dev/null 2>> warnings.log
[[ $(docker compose exec -T postgres_db psql -X -U "$admin_user" -d postgres -At -c 'SELECT count(*) FROM synthetic_vectors' 2>> warnings.log) == 2 ]]
echo "PASS: $source_image ($scenario, $admin_user): automatic startup, socket-only bootstrap, preserved runtime/data, cache reuse, failure gate, and recovery"
