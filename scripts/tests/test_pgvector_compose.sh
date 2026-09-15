#!/usr/bin/env bash
# Check persistent image selection with no pgvector Compose override.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work_dir=$(mktemp -d)
cleanup() {
    result=$?
    if [[ $result != 0 && -f "$work_dir/warnings.log" ]]; then
        tail -5 "$work_dir/warnings.log" >&2
    fi
    rm -rf "$work_dir"
}
trap cleanup EXIT
# Do not let the developer shell override the synthetic .env under test.
unset TRACECAT__PGVECTOR_IMAGE COMPOSE_FILE COMPOSE_PROJECT_NAME
for compose_file in docker-compose.yml docker-compose.local.yml docker-compose.dev.yml; do
    cp "$repo_root/$compose_file" "$work_dir/compose.yaml"
    printf 'COMPOSE_PROJECT_NAME=pgvector-config-test\nTRACECAT__LOCAL_REPOSITORY_PATH=/tmp/synthetic-registry\nPUBLIC_APP_PORT=8080\nTEMPORAL__UI_VERSION=latest\n' > "$work_dir/.env"
    (cd "$work_dir" && docker compose config --format json) > "$work_dir/default.json" 2> "$work_dir/warnings.log"
    printf 'TRACECAT__PGVECTOR_IMAGE=synthetic-postgres-pgvector:validated\n' >> "$work_dir/.env"
    (cd "$work_dir" && docker compose config --format json) > "$work_dir/configured.json" 2>> "$work_dir/warnings.log"
    python3 - "$work_dir" "$repo_root/deployments/postgres/images.json" <<'PY'
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
default = json.loads((root / 'default.json').read_text())
configured = json.loads((root / 'configured.json').read_text())
expected_image = json.loads(Path(sys.argv[2]).read_text())['trixie']['image']
assert default['services']['postgres_db']['image'] == expected_image
assert configured['services']['postgres_db']['image'] == 'synthetic-postgres-pgvector:validated'
configured['services']['postgres_db']['image'] = expected_image
assert 'pgvector_setup' not in default['services']
assert default['services']['migrations']['depends_on']['postgres_db']['condition'] == 'service_healthy'
postgres = default['services']['postgres_db']
assert postgres.get('entrypoint') is None
assert postgres['environment']['POSTGRES_DB'] == 'postgres'
assert all(volume['type'] == 'volume' for volume in postgres['volumes'])
assert 'pgvector-cache' not in default['volumes']
assert '-h 127.0.0.1' in postgres['healthcheck']['test'][1]
networks = default.get('networks', {})
assert 'postgres-egress' not in networks
assert configured == default, 'Image selection changed unrelated Compose settings'
PY
    echo "PASS: $compose_file reads persistent .env image; all other settings unchanged"
done
