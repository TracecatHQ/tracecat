#!/usr/bin/env bash
# Print publish for an absent tag, skip for an exact retry, or fail closed.
set -euo pipefail
image=${1:?Usage: check-unpublished-image.sh IMAGE AMD64_REF ARM64_REF}
amd64=${2:?AMD64_REF is required}
arm64=${3:?ARM64_REF is required}
[[ "$image" =~ ^ghcr.io/tracecathq/tracecat-postgres:([1-9][0-9]*(-bookworm)?)$ ]]
tag=${BASH_REMATCH[1]}
for ref in "$amd64" "$arm64"; do
    [[ "$ref" =~ ^ghcr.io/tracecathq/tracecat-postgres@sha256:[a-f0-9]{64}$ ]]
done
: "${GITHUB_ACTOR:?GITHUB_ACTOR is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
manifest=$(mktemp)
trap 'rm -f "$manifest"' EXIT
token=$(curl --fail --silent --show-error --max-time 30 \
    --user "$GITHUB_ACTOR:$GITHUB_TOKEN" \
    'https://ghcr.io/token?service=ghcr.io&scope=repository:tracecathq/tracecat-postgres:pull' | jq -er '.token')
status=$(curl --silent --show-error --max-time 30 --output "$manifest" --write-out '%{http_code}' \
    --header "Authorization: Bearer $token" \
    --header 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json' \
    "https://ghcr.io/v2/tracecathq/tracecat-postgres/manifests/$tag")
case "$status" in
    404) echo publish ;;
    200)
        # A completed attempt is a no-op only when both platform digests match
        # the images loaded from this run's tested artifacts. Source SHA alone
        # is insufficient: a rebuild may produce different image contents.
        if jq -e --arg amd64 "${amd64##*@}" --arg arm64 "${arm64##*@}" '
            .schemaVersion == 2 and
            (.mediaType == "application/vnd.oci.image.index.v1+json" or
             .mediaType == "application/vnd.docker.distribution.manifest.list.v2+json") and
            (.manifests | length == 2) and
            ([.manifests[] | {digest, os: .platform.os, arch: .platform.architecture}]
             | sort_by(.arch)) == [
                {digest: $amd64, os: "linux", arch: "amd64"},
                {digest: $arm64, os: "linux", arch: "arm64"}
             ]
        ' "$manifest" > /dev/null; then
            echo skip
        else
            echo "Refusing to overwrite $image with different or unverified contents. Bump the revision in images.json and Compose defaults." >&2
            exit 1
        fi
        ;;
    *)
        echo "Cannot verify image revision availability (HTTP $status); refusing publication." >&2
        exit 1
        ;;
esac
