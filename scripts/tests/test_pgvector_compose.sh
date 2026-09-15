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
    python3 - "$work_dir" <<'PY'
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
default = json.loads((root / 'default.json').read_text())
configured = json.loads((root / 'configured.json').read_text())
assert default['services']['postgres_db']['image'] == 'postgres:16'
assert configured['services']['postgres_db']['image'] == 'synthetic-postgres-pgvector:validated'
configured['services']['postgres_db']['image'] = 'postgres:16'
assert configured == default, 'Image selection changed unrelated Compose settings'
PY
    echo "PASS: $compose_file reads persistent .env image; all other settings unchanged"
done
