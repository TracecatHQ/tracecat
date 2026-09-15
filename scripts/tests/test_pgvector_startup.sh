#!/usr/bin/env bash
# Real Compose startup using the production wiring and a small SQL migration probe.
# No image build, pgvector override, or manual extension installation.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work_dir=$(mktemp -d)
project="pgvector-startup-$(date +%s)-$$"
source_image=${1:-postgres:16}
scenario=${2:-existing}
unset COMPOSE_FILE TRACECAT__PGVECTOR_IMAGE
export COMPOSE_PROJECT_NAME="$project"
export TRACECAT__POSTGRES_USER=postgres TRACECAT__POSTGRES_PASSWORD=synthetic-test-password
cp "$repo_root/docker-compose.dev.yml" "$work_dir/compose.yaml"
mkdir -p "$work_dir/scripts/postgres"
cp "$repo_root/scripts/postgres/"{compose-entrypoint.sh,pgvector.sql} "$work_dir/scripts/postgres/"
cat > "$work_dir/.env" <<ENV
TRACECAT__LOCAL_REPOSITORY_PATH=/tmp/synthetic-registry
PUBLIC_APP_PORT=8080
TEMPORAL__UI_VERSION=latest
ENV
cd "$work_dir"
cleanup() {
    result=$?
    if [[ $result != 0 ]]; then
        tail -8 "$work_dir/warnings.log" >&2 || true
        docker compose logs postgres_db pgvector_setup migrations 2>/dev/null | tail -60 >&2 || true
    fi
    docker compose down >/dev/null 2>&1 || true
    docker rm -f "$project-seed" >/dev/null 2>&1 || true
    echo "Retained synthetic volumes for $project"
    rm -rf "$work_dir"
}
trap cleanup EXIT
docker compose config --format json > rendered.json 2> warnings.log
python3 - "$source_image" <<'PY'
import json
import sys
from pathlib import Path
config = json.loads(Path('rendered.json').read_text())
config['services'] = {k: config['services'][k] for k in ('postgres_db', 'pgvector_setup', 'migrations')}
postgres = config['services']['postgres_db']
assert postgres['image'] == 'postgres:16', 'Default startup must need no image setting'
postgres['image'] = sys.argv[1]  # Test another supported distribution when requested.
postgres.pop('ports', None)
setup = config['services']['pgvector_setup']
setup['image'] = sys.argv[1]
config['services']['migrations'] = {
    'image': sys.argv[1],
    'environment': setup['environment'],
    'entrypoint': ['psql', '-X', '-v', 'ON_ERROR_STOP=1', '-c',
        "CREATE TABLE IF NOT EXISTS synthetic_vectors (embedding vector(3)); INSERT INTO synthetic_vectors VALUES ('[1,0,0]');"],
    'depends_on': config['services']['migrations']['depends_on'],
}
config['volumes'] = {k: config['volumes'][k] for k in ('core-db', 'pgvector-cache')}
Path('compose.yaml').write_text(json.dumps(config))
PY
if [[ "$scenario" == existing ]]; then
    # Create a source row with the unmodified plain image first.
    docker run -d --name "$project-seed" -e POSTGRES_PASSWORD=synthetic-test-password \
        -v "${project}_core-db:/var/lib/postgresql/data" "$source_image" >/dev/null
    for ((i=0;i<60;i++)); do
        if docker exec "$project-seed" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1; then break; fi
        sleep 1
    done
    docker exec "$project-seed" psql -X -U postgres -v ON_ERROR_STOP=1 \
        -c "CREATE TABLE synthetic_source (body text); INSERT INTO synthetic_source VALUES ('preserved');" >/dev/null
    docker stop "$project-seed" >/dev/null
    docker rm "$project-seed" >/dev/null
fi
# The dependency chain must enable vector before the migration probe can run.
docker compose up -d migrations > /dev/null 2>> warnings.log
docker compose wait migrations
[[ $(docker compose exec -T postgres_db psql -X -U postgres -At -c 'SELECT count(*) FROM synthetic_vectors' 2>> warnings.log) == 1 ]]
if [[ "$scenario" == existing ]]; then
    [[ $(docker compose exec -T postgres_db psql -X -U postgres -At -c 'SELECT body FROM synthetic_source' 2>> warnings.log) == preserved ]]
fi
container=$(docker compose ps -q postgres_db 2>> warnings.log)
# Installing the extension files must not alter the base's installed packages.
before=$(docker run --rm --entrypoint bash "$source_image" -c 'dpkg-query -W; sha256sum "$(command -v postgres)"; find /usr/lib /lib -type f \( -name "libicu*.so.*" -o -name "libc.so.6" \) -exec sha256sum {} + | sort')
after=$(docker exec "$container" bash -c 'dpkg-query -W; sha256sum "$(command -v postgres)"; find /usr/lib /lib -type f \( -name "libicu*.so.*" -o -name "libc.so.6" \) -exec sha256sum {} + | sort')
[[ "$before" == "$after" ]]
# Recreating the container must restore extension files from cache and be idempotent.
docker compose up -d --force-recreate postgres_db > /dev/null 2>> warnings.log
docker compose up -d --force-recreate migrations > /dev/null 2>> warnings.log
docker compose wait migrations
docker compose logs postgres_db 2>> warnings.log | grep -F 'Using cached pgvector 0.8.6 package.' > /dev/null
[[ $(docker compose exec -T postgres_db psql -X -U postgres -At -c 'SELECT count(*) FROM synthetic_vectors' 2>> warnings.log) == 2 ]]
# A failed setup must prevent the migration probe from inserting another row.
docker compose rm -f migrations pgvector_setup > /dev/null 2>> warnings.log
printf '\\set ON_ERROR_STOP on\nSELECT 1/0;\n' > scripts/postgres/pgvector.sql
if docker compose up -d migrations > /dev/null 2>> warnings.log; then
    echo 'Expected failed provisioning to block migrations' >&2
    exit 1
fi
[[ $(docker compose exec -T postgres_db psql -X -U postgres -At -c 'SELECT count(*) FROM synthetic_vectors' 2>> warnings.log) == 2 ]]
echo "PASS: $source_image ($scenario): automatic startup, preserved runtime/data, cache reuse, and migration failure gate"
