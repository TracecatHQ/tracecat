#!/usr/bin/env bash
# Build the same versioned image as the publisher, without publishing anything.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
variant=${1:-trixie}
base=$(jq -er --arg variant "$variant" '.[$variant].base' "$repo_root/deployments/postgres/images.json")
image=$(jq -er --arg variant "$variant" '.[$variant].image' "$repo_root/deployments/postgres/images.json")
docker build --load --build-arg "BASE_IMAGE=$base" \
    -f "$repo_root/deployments/postgres/Dockerfile" -t "$image" "$repo_root"
