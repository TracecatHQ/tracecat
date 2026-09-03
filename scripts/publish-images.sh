#!/usr/bin/env bash

set -euo pipefail

SCRIPT_NAME=$(basename "$0")
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

REGISTRY=${REGISTRY:-ghcr.io}
IMAGE_NAMESPACE=${IMAGE_NAMESPACE:-tracecathq}
SOURCE_URL=${SOURCE_URL:-https://github.com/TracecatHQ/tracecat}
PLATFORMS_CSV=${PLATFORMS:-linux/amd64,linux/arm64}
IMAGE_SELECTION=all
BUILDER=
DRY_RUN=0
YES=0
ALLOW_DIRTY=0
ADD_SHA_TAG=1
LATEST_MODE=auto
SEMVER_ALIASES=auto
CLEANUP_DIGEST_DIR=

NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL:-http://localhost:8000}
NEXT_PUBLIC_APP_ENV=${NEXT_PUBLIC_APP_ENV:-production}
NEXT_PUBLIC_APP_URL=${NEXT_PUBLIC_APP_URL:-http://localhost:3000}
NEXT_SERVER_API_URL=${NEXT_SERVER_API_URL:-http://localhost:8000}
NODE_ENV=${NODE_ENV:-production}

declare -a USER_TAGS=()
declare -a TAGS=()
declare -a PLATFORMS=()
declare -a IMAGE_KEYS=()

usage() {
  cat <<'EOF'
Usage: scripts/publish-images.sh --tag TAG [options]

Break-glass publisher for Tracecat images. It mirrors the GitHub Actions
Publish images workflow:

  1. build each platform image by digest
  2. push the immutable digest images
  3. create one multi-arch manifest for the final tags
  4. inspect the published manifest

Options:
  --tag TAG                 Tag to publish. Repeat to publish multiple tags.
                            If omitted, the script uses the exact git tag at HEAD,
                            or the current staging/preview branch.
  --image api|ui|all        Image set to publish. Default: all.
  --platforms CSV           Platforms to build. Default: linux/amd64,linux/arm64.
  --registry REGISTRY       Registry host. Default: ghcr.io.
  --namespace NAMESPACE     Image namespace. Default: tracecathq.
  --builder NAME            Docker buildx builder to use.
  --latest                  Always add the latest tag.
  --no-latest               Do not add the latest tag.
  --no-sha-tag              Do not add the sha-<commit> tag.
  --no-semver-aliases       Do not add stable semver aliases like 1.2.
  --allow-dirty             Allow publishing from a dirty worktree.
  -y, --yes                 Skip the confirmation prompt.
  -n, --dry-run             Print the Docker commands without running them.
  -h, --help                Show this help.

Environment overrides:
  REGISTRY, IMAGE_NAMESPACE, PLATFORMS
  NEXT_PUBLIC_API_URL, NEXT_PUBLIC_APP_ENV, NEXT_PUBLIC_APP_URL,
  NEXT_SERVER_API_URL, NODE_ENV

Examples:
  scripts/publish-images.sh --tag 1.2.3 --yes
  scripts/publish-images.sh --tag 1.2.3-beta.0 --image api
  scripts/publish-images.sh --tag nightly-20260519 --no-latest

Before running for real, authenticate Docker for GHCR:
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GITHUB_USER" --password-stdin
EOF
}

abort() {
  echo "[$SCRIPT_NAME] $1" >&2
  exit "${2:-1}"
}

log() {
  echo "[$SCRIPT_NAME] $1"
}

run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [[ $DRY_RUN -eq 0 ]]; then
    "$@"
  fi
}

require_command() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || abort "Missing required command: $cmd"
}

contains_value() {
  local needle="$1"
  local item
  shift || true
  for item in "$@"; do
    [[ "$item" == "$needle" ]] && return 0
  done
  return 1
}

validate_tag() {
  local tag="$1"
  [[ "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] \
    || abort "Invalid Docker tag: $tag"
}

add_tag() {
  local tag="$1"
  validate_tag "$tag"
  if [[ ${#TAGS[@]} -eq 0 ]] || ! contains_value "$tag" "${TAGS[@]}"; then
    TAGS+=("$tag")
  fi
}

is_release_tag() {
  local tag="${1#v}"
  [[ "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)*$ ]]
}

is_nightly_tag() {
  [[ "$1" == nightly-* ]]
}

add_semver_aliases() {
  local tag="${1#v}"
  local major minor
  if [[ "$SEMVER_ALIASES" == auto && "$tag" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
    add_tag "$tag"
    add_tag "$major.$minor"
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tag)
        [[ $# -gt 1 ]] || abort "Missing value for $1."
        USER_TAGS+=("$2")
        shift 2
        ;;
      --image)
        [[ $# -gt 1 ]] || abort "Missing value for $1."
        IMAGE_SELECTION="$2"
        shift 2
        ;;
      --platforms)
        [[ $# -gt 1 ]] || abort "Missing value for $1."
        PLATFORMS_CSV="$2"
        shift 2
        ;;
      --registry)
        [[ $# -gt 1 ]] || abort "Missing value for $1."
        REGISTRY="$2"
        shift 2
        ;;
      --namespace)
        [[ $# -gt 1 ]] || abort "Missing value for $1."
        IMAGE_NAMESPACE="$2"
        shift 2
        ;;
      --builder)
        [[ $# -gt 1 ]] || abort "Missing value for $1."
        BUILDER="$2"
        shift 2
        ;;
      --latest)
        LATEST_MODE=always
        shift
        ;;
      --no-latest)
        LATEST_MODE=never
        shift
        ;;
      --no-sha-tag)
        ADD_SHA_TAG=0
        shift
        ;;
      --no-semver-aliases)
        SEMVER_ALIASES=never
        shift
        ;;
      --allow-dirty)
        ALLOW_DIRTY=1
        shift
        ;;
      -y|--yes)
        YES=1
        shift
        ;;
      -n|--dry-run)
        DRY_RUN=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        abort "Unknown argument: $1"
        ;;
    esac
  done
}

resolve_platforms() {
  local cleaned_csv="${PLATFORMS_CSV// /}"
  local platform
  IFS=',' read -r -a PLATFORMS <<< "$cleaned_csv"
  [[ ${#PLATFORMS[@]} -gt 0 && -n "${PLATFORMS[0]}" ]] || abort "No platforms specified."

  for platform in "${PLATFORMS[@]}"; do
    [[ "$platform" == linux/* ]] || abort "Unsupported platform format: $platform"
  done
}

resolve_images() {
  case "$IMAGE_SELECTION" in
    api)
      IMAGE_KEYS=(api)
      ;;
    ui)
      IMAGE_KEYS=(ui)
      ;;
    all)
      IMAGE_KEYS=(api ui)
      ;;
    *)
      abort "Invalid --image value: $IMAGE_SELECTION"
      ;;
  esac
}

infer_tags() {
  local branch exact_tag
  if [[ ${#USER_TAGS[@]} -eq 0 ]]; then
    exact_tag=$(git -C "$REPO_ROOT" describe --tags --exact-match 2>/dev/null || true)
    if [[ -n "$exact_tag" ]]; then
      USER_TAGS+=("$exact_tag")
    else
      branch=$(git -C "$REPO_ROOT" branch --show-current)
      if [[ "$branch" == staging || "$branch" == preview ]]; then
        USER_TAGS+=("$branch")
      else
        abort "Provide --tag TAG, or run from an exact git tag / staging / preview."
      fi
    fi
  fi
}

resolve_tags() {
  local tag primary_tag short_sha
  primary_tag="${USER_TAGS[0]}"

  for tag in "${USER_TAGS[@]}"; do
    add_tag "$tag"
    add_semver_aliases "$tag"
  done

  short_sha=$(git -C "$REPO_ROOT" rev-parse --short=7 HEAD)
  if [[ $ADD_SHA_TAG -eq 1 ]]; then
    add_tag "sha-$short_sha"
  fi

  case "$LATEST_MODE" in
    always)
      add_tag latest
      ;;
    never)
      ;;
    auto)
      if is_release_tag "$primary_tag" && ! is_nightly_tag "$primary_tag"; then
        add_tag latest
      fi
      ;;
    *)
      abort "Internal error: invalid LATEST_MODE=$LATEST_MODE"
      ;;
  esac
}

image_ref_for_key() {
  case "$1" in
    api)
      printf '%s/%s/tracecat\n' "$REGISTRY" "$IMAGE_NAMESPACE"
      ;;
    ui)
      printf '%s/%s/tracecat-ui\n' "$REGISTRY" "$IMAGE_NAMESPACE"
      ;;
    *)
      abort "Internal error: unknown image key $1"
      ;;
  esac
}

platform_tag() {
  local platform="$1"
  printf '%s\n' "${platform//\//-}"
}

check_worktree_clean() {
  if [[ $ALLOW_DIRTY -eq 1 ]]; then
    return 0
  fi

  if [[ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ]]; then
    abort "Refusing to publish from a dirty worktree. Commit/stash changes or pass --allow-dirty."
  fi
}

preflight() {
  require_command docker
  require_command git
  require_command jq

  git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null
  check_worktree_clean

  if [[ -n "$BUILDER" ]]; then
    run docker buildx inspect "$BUILDER" --bootstrap
  else
    run docker buildx inspect --bootstrap
  fi
}

print_plan() {
  local image_key tag platform
  local commit
  commit=$(git -C "$REPO_ROOT" rev-parse HEAD)

  log "Plan"
  echo "  Commit: $commit"
  echo "  Images:"
  for image_key in "${IMAGE_KEYS[@]}"; do
    echo "    - $(image_ref_for_key "$image_key")"
  done
  echo "  Platforms:"
  for platform in "${PLATFORMS[@]}"; do
    echo "    - $platform"
  done
  echo "  Tags:"
  for tag in "${TAGS[@]}"; do
    echo "    - $tag"
  done
}

confirm_publish() {
  local reply
  if [[ $YES -eq 1 || $DRY_RUN -eq 1 ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    abort "Refusing to publish non-interactively without --yes."
  fi
  read -r -p "Publish these image tags to $REGISTRY? [y/N]: " reply
  [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]] || abort "Cancelled."
}

common_build_flags() {
  local image_ref="$1"
  local platform="$2"
  local ptag="$3"
  local metadata_file="$4"
  local created_at git_sha version_label

  created_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
  git_sha=$(git -C "$REPO_ROOT" rev-parse HEAD)
  version_label="${USER_TAGS[0]}"

  BUILD_CMD+=(
    --platform "$platform"
    --label "org.opencontainers.image.created=$created_at"
    --label "org.opencontainers.image.revision=$git_sha"
    --label "org.opencontainers.image.source=$SOURCE_URL"
    --label "org.opencontainers.image.version=$version_label"
    --output "type=image,name=$image_ref,push-by-digest=true,name-canonical=true,push=true"
    --cache-from "type=registry,ref=$image_ref:buildcache-$ptag"
    --cache-to "type=registry,ref=$image_ref:buildcache-$ptag,mode=max,ignore-error=true"
    --metadata-file "$metadata_file"
  )
}

build_platform_digest() {
  local image_key="$1"
  local image_ref="$2"
  local platform="$3"
  local digest_dir="$4"
  local ptag metadata_file digest
  ptag=$(platform_tag "$platform")
  metadata_file="$digest_dir/$image_key-$ptag.metadata.json"

  BUILD_CMD=(docker buildx build)
  if [[ -n "$BUILDER" ]]; then
    BUILD_CMD+=(--builder "$BUILDER")
  fi
  common_build_flags "$image_ref" "$platform" "$ptag" "$metadata_file"

  case "$image_key" in
    api)
      BUILD_CMD+=(--target production "$REPO_ROOT")
      ;;
    ui)
      BUILD_CMD+=(
        --file "$REPO_ROOT/frontend/Dockerfile.prod"
        --build-arg "NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL"
        --build-arg "NEXT_PUBLIC_APP_ENV=$NEXT_PUBLIC_APP_ENV"
        --build-arg "NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL"
        --build-arg "NEXT_SERVER_API_URL=$NEXT_SERVER_API_URL"
        --build-arg "NODE_ENV=$NODE_ENV"
        "$REPO_ROOT/frontend"
      )
      ;;
    *)
      abort "Internal error: unknown image key $image_key"
      ;;
  esac

  log "Building $image_ref for $platform"
  run "${BUILD_CMD[@]}"

  if [[ $DRY_RUN -eq 1 ]]; then
    digest="sha256:0000000000000000000000000000000000000000000000000000000000000000"
  else
    digest=$(jq -r '."containerimage.digest" // empty' "$metadata_file")
    [[ "$digest" == sha256:* ]] || abort "Could not read image digest from $metadata_file"
  fi

  printf '%s\n' "$digest" > "$digest_dir/$image_key-$ptag.digest"
}

publish_manifest() {
  local image_key="$1"
  local image_ref="$2"
  local digest_dir="$3"
  local tag digest_file digest inspect_tag
  local -a cmd=()

  cmd=(docker buildx imagetools create)
  for tag in "${TAGS[@]}"; do
    cmd+=(-t "$image_ref:$tag")
  done

  for digest_file in "$digest_dir"/"$image_key"-*.digest; do
    digest=$(<"$digest_file")
    cmd+=("$image_ref@$digest")
  done

  log "Publishing manifest for $image_ref"
  run "${cmd[@]}"

  inspect_tag="${USER_TAGS[0]}"
  log "Inspecting $image_ref:$inspect_tag"
  run docker buildx imagetools inspect "$image_ref:$inspect_tag"
}

main() {
  local digest_dir image_key image_ref platform

  parse_args "$@"
  resolve_platforms
  resolve_images
  infer_tags
  resolve_tags
  print_plan
  confirm_publish
  preflight

  CLEANUP_DIGEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/tracecat-publish-images.XXXXXX")
  digest_dir="$CLEANUP_DIGEST_DIR"
  trap 'rm -rf "$CLEANUP_DIGEST_DIR"' EXIT

  for image_key in "${IMAGE_KEYS[@]}"; do
    image_ref=$(image_ref_for_key "$image_key")
    for platform in "${PLATFORMS[@]}"; do
      build_platform_digest "$image_key" "$image_ref" "$platform" "$digest_dir"
    done
    publish_manifest "$image_key" "$image_ref" "$digest_dir"
  done
}

main "$@"
