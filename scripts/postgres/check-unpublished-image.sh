#!/usr/bin/env bash
# Fail closed unless GHCR confirms this versioned manifest does not exist.
set -euo pipefail
image=${1:?Usage: check-unpublished-image.sh IMAGE}
[[ "$image" =~ ^ghcr.io/tracecathq/tracecat-postgres:([1-9][0-9]*(-bookworm)?)$ ]]
tag=${BASH_REMATCH[1]}
: "${GITHUB_ACTOR:?GITHUB_ACTOR is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
token=$(curl --fail --silent --show-error --max-time 30 \
    --user "$GITHUB_ACTOR:$GITHUB_TOKEN" \
    'https://ghcr.io/token?service=ghcr.io&scope=repository:tracecathq/tracecat-postgres:pull' | jq -er '.token')
status=$(curl --silent --show-error --max-time 30 --output /dev/null --write-out '%{http_code}' \
    --header "Authorization: Bearer $token" \
    --header 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json' \
    "https://ghcr.io/v2/tracecathq/tracecat-postgres/manifests/$tag")
case "$status" in
    404) echo "Image revision is available: $image" ;;
    200)
        echo "Refusing to overwrite $image. Bump the revision in images.json and Compose defaults." >&2
        exit 1
        ;;
    *)
        echo "Cannot verify image revision availability (HTTP $status); refusing publication." >&2
        exit 1
        ;;
esac
