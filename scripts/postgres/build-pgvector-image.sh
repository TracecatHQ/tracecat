#!/usr/bin/env bash
# Build on the immutable image used by a container, or a locally available image.
# Never pulls a floating tag or changes a running database.
set -euo pipefail
if [[ $# != 3 || ( "$1" != --container && "$1" != --image ) ]]; then
    echo "Usage: $0 --container CONTAINER OUTPUT_IMAGE | --image LOCAL_IMAGE OUTPUT_IMAGE" >&2
    exit 1
fi
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "$1" == --container ]]; then
    image_id=$(docker container inspect --format '{{.Image}}' "$2")
else
    image_id=$(docker image inspect --format '{{.Id}}' "$2")
fi
base_image=$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "$image_id")
if [[ -z "$base_image" ]]; then
    echo 'The base has no registry digest. Publish the exact base image to your registry before building.' >&2
    exit 1
fi
# Refuse a digest that does not resolve to the selected local image.
[[ $(docker image inspect --format '{{.Id}}' "$base_image") == "$image_id" ]]
echo "Building from immutable base: $base_image"
docker build --load --build-arg "BASE_IMAGE=$base_image" \
    -f "$repo_root/deployments/postgres/Dockerfile" -t "$3" \
    "$repo_root/deployments/postgres"
