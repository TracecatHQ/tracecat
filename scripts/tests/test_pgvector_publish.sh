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
    while [[ $# -gt 0 ]]; do
        if [[ "$1" == --output ]]; then
            printf '%s' "$REGISTRY_MANIFEST" > "$2"
            shift
        fi
        shift
    done
    printf '%s' "$REGISTRY_STATUS"
fi
MOCK
chmod +x "$scratch/curl"
export PATH="$scratch:$PATH" GITHUB_ACTOR=synthetic GITHUB_TOKEN=synthetic
amd64="sha256:$(printf 'a%.0s' {1..64})"
arm64="sha256:$(printf 'b%.0s' {1..64})"
manifest=$(jq -cn --arg amd64 "$amd64" --arg arm64 "$arm64" '{
    schemaVersion: 2, mediaType: "application/vnd.oci.image.index.v1+json",
    manifests: [
        {digest: $amd64, platform: {os: "linux", architecture: "amd64"}},
        {digest: $arm64, platform: {os: "linux", architecture: "arm64"}}
    ]
}')
export REGISTRY_MANIFEST="$manifest"
check() {
    bash "$repo_root/scripts/postgres/check-unpublished-image.sh" "$image" \
        "ghcr.io/tracecathq/tracecat-postgres@$amd64" \
        "ghcr.io/tracecathq/tracecat-postgres@$arm64"
}
for variant in 1 1-bookworm; do
    image="ghcr.io/tracecathq/tracecat-postgres:$variant"
    [[ $(REGISTRY_STATUS=404 check) == publish ]]
    # A completed variant can be retried while the other variant is missing.
    [[ $(REGISTRY_STATUS=200 check) == skip ]]
    REGISTRY_MANIFEST=$(jq '.manifests |= reverse' <<< "$manifest")
    [[ $(REGISTRY_STATUS=200 check) == skip ]]
    REGISTRY_MANIFEST=$(jq '.mediaType = "application/vnd.docker.distribution.manifest.list.v2+json"' <<< "$manifest")
    [[ $(REGISTRY_STATUS=200 check) == skip ]]
    for mutation in \
        '.manifests[0].digest = "sha256:different"' \
        '.manifests[0].platform.architecture = "arm64"' \
        '.manifests[0].platform.os = "windows"' \
        '.manifests += [.manifests[0]]' \
        '.manifests |= .[:1]' \
        '.schemaVersion = 1' \
        '.mediaType = "application/vnd.oci.image.manifest.v1+json"'; do
        REGISTRY_MANIFEST=$(jq "$mutation" <<< "$manifest")
        if REGISTRY_STATUS=200 check; then
            echo "Unexpectedly accepted different manifest: $mutation" >&2
            exit 1
        fi
    done
    REGISTRY_MANIFEST='invalid JSON'
    if REGISTRY_STATUS=200 check; then
        echo "Unexpectedly accepted malformed manifest" >&2
        exit 1
    fi
    REGISTRY_MANIFEST="$manifest"
    for status in 401 403 429 500; do
        if REGISTRY_STATUS=$status check; then
            echo "Unexpectedly permitted publication for HTTP $status" >&2
            exit 1
        fi
    done
    for failure in TOKEN_FAILURE NETWORK_FAILURE; do
        export "$failure=true"
        if REGISTRY_STATUS=404 check; then
            echo "Unexpectedly permitted publication after $failure" >&2
            exit 1
        fi
        unset "$failure"
    done
done
echo "Publication guard tests passed"

# Verify the final-tag writer consumes the guard result, including a create
# that succeeded remotely but whose response was lost locally.
cat > "$scratch/docker" <<'MOCK'
#!/usr/bin/env bash
[[ "$1 $2 $3" == 'buildx imagetools create' ]] || exit 1
printf '%s\n' "$*" >> "$CREATE_LOG"
[[ ${LOST_RESPONSE:-false} == false ]] || exit 7
MOCK
chmod +x "$scratch/docker"
export CREATE_LOG="$scratch/creates"
publish() {
    bash "$repo_root/scripts/postgres/publish-manifest.sh" "$image" \
        "ghcr.io/tracecathq/tracecat-postgres@$amd64" \
        "ghcr.io/tracecathq/tracecat-postgres@$arm64"
}
for variant in 1 1-bookworm; do
    image="ghcr.io/tracecathq/tracecat-postgres:$variant"
    : > "$CREATE_LOG"
    REGISTRY_MANIFEST="$manifest"
    REGISTRY_STATUS=200 publish
    [[ ! -s "$CREATE_LOG" ]]
    REGISTRY_STATUS=404 publish
    [[ $(wc -l < "$CREATE_LOG") -eq 1 ]]
    [[ $(cat "$CREATE_LOG") == "buildx imagetools create -t $image ghcr.io/tracecathq/tracecat-postgres@$amd64 ghcr.io/tracecathq/tracecat-postgres@$arm64" ]]
    REGISTRY_MANIFEST='{}'
    if REGISTRY_STATUS=200 publish; then
        echo "Unexpectedly published a mismatched manifest" >&2
        exit 1
    fi
    [[ $(wc -l < "$CREATE_LOG") -eq 1 ]]
    REGISTRY_MANIFEST="$manifest"
    if LOST_RESPONSE=true REGISTRY_STATUS=404 publish; then
        echo "Expected the simulated lost response to fail" >&2
        exit 1
    fi
    [[ $(wc -l < "$CREATE_LOG") -eq 2 ]]
    REGISTRY_STATUS=200 publish
    [[ $(wc -l < "$CREATE_LOG") -eq 2 ]]
done
echo "Publication retry tests passed"
