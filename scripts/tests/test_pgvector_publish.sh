#!/usr/bin/env bash
# Exercise registry responses without registry access or real credentials.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
cat > "$scratch/curl" <<'MOCK'
#!/usr/bin/env bash
if [[ "$*" == *ghcr.io/token* ]]; then
    [[ ${TOKEN_FAILURE:-false} == false ]] || exit 22
    echo '{"token":"synthetic-token"}'
else
    [[ ${NETWORK_FAILURE:-false} == false ]] || exit 7
    printf '%s' "$REGISTRY_STATUS"
fi
MOCK
chmod +x "$scratch/curl"
export PATH="$scratch:$PATH" GITHUB_ACTOR=synthetic GITHUB_TOKEN=synthetic
for variant in 1 1-bookworm; do
    image="ghcr.io/tracecathq/tracecat-postgres:$variant"
    REGISTRY_STATUS=404 bash "$repo_root/scripts/postgres/check-unpublished-image.sh" "$image"
    for status in 200 401 403 429 500; do
        if REGISTRY_STATUS=$status bash "$repo_root/scripts/postgres/check-unpublished-image.sh" "$image"; then
            echo "Unexpectedly permitted publication for HTTP $status" >&2
            exit 1
        fi
    done
    for failure in TOKEN_FAILURE NETWORK_FAILURE; do
        if env "$failure=true" REGISTRY_STATUS=404 bash "$repo_root/scripts/postgres/check-unpublished-image.sh" "$image"; then
            echo "Unexpectedly permitted publication after $failure" >&2
            exit 1
        fi
    done
done
echo "Publication guard tests passed"
