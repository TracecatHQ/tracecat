#!/usr/bin/env bash
# Publish a missing immutable tag, or safely skip an exact completed attempt.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
image=${1:?Usage: publish-manifest.sh IMAGE AMD64_REF ARM64_REF}
decision=$(bash "$script_dir/check-unpublished-image.sh" "$@")
case "$decision" in
    publish) docker buildx imagetools create -t "$image" "$2" "$3" ;;
    skip) echo "Already published the same tested architectures: $image" ;;
    *) echo "Unknown publication decision; refusing publication." >&2; exit 1 ;;
esac
