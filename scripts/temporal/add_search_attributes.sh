#!/usr/bin/env bash

set -euo pipefail

SCRIPT_NAME=$(basename "$0")
TEMP_API_KEY=${TEMPORAL_API_KEY:-}
DRY_RUN=0
NON_INTERACTIVE=0
declare -a SEARCH_ATTRIBUTES=()

usage() {
  cat <<'EOF'
Usage: scripts/temporal/add_search_attributes.sh [options] --attribute Name=Type [--attribute Name=Type ...]

Adds Temporal Cloud search attributes to every namespace accessible by the provided API key.

Options:
  -a, --attribute Name=Type   Attribute specification (e.g. MyAttr=Text). Repeatable.
  -d, --dry-run               Show the commands without executing them.
      --non-interactive       Skip the confirmation prompt.
  -h, --help                  Show this message.

Required environment variables:
  TEMPORAL_API_KEY            Temporal Cloud API key with namespace admin privileges.
EOF
}

abort() {
  echo "[$SCRIPT_NAME] $1" >&2
  exit "${2:-1}"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -a|--attribute)
        [[ $# -gt 1 ]] || abort "Missing value for $1."
        SEARCH_ATTRIBUTES+=("$2")
        shift 2
        ;;
      -d|--dry-run)
        DRY_RUN=1
        shift
        ;;
      --non-interactive)
        NON_INTERACTIVE=1
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

require_command() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || abort "Missing required command: $cmd"
}

fetch_namespaces() {
  local output
  output=$(tcld --api-key="$TEMP_API_KEY" namespace list)
  # Normalise different response structures into a flat list of namespace names.
  echo "$output" | jq -r '.namespaces[]'
}

confirm() {
  local prompt="$1"
  if [[ $NON_INTERACTIVE -eq 1 ]]; then
    return 0
  fi
  read -r -p "$prompt [y/N]: " reply
  [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

main() {
  parse_args "$@"

  [[ -n "$TEMP_API_KEY" ]] || abort "TEMPORAL_API_KEY is not set."
  (( ${#SEARCH_ATTRIBUTES[@]} )) || abort "At least one --attribute Name=Type pair is required."

  require_command "tcld"
  require_command "jq"

  local -a namespaces=()
  while IFS= read -r ns; do
    [[ -n "$ns" ]] && namespaces+=("$ns")
  done < <(fetch_namespaces)
  (( ${#namespaces[@]} )) || abort "No namespaces found for the provided API key."

  echo "Discovered namespaces:"
  for ns in "${namespaces[@]}"; do
    echo "  - $ns"
  done

  echo
  echo "Search attributes to add:"
  for sa in "${SEARCH_ATTRIBUTES[@]}"; do
    echo "  - $sa"
  done

  echo
  if ! confirm "Proceed with adding search attributes to all namespaces?"; then
    echo "Aborted."
    exit 0
  fi

  local failures=0
  for namespace in "${namespaces[@]}"; do
    for attr in "${SEARCH_ATTRIBUTES[@]}"; do
      echo
      echo "[$namespace] Adding search attribute: $attr"
      if [[ $DRY_RUN -eq 1 ]]; then
        echo "DRY-RUN: tcld --api-key=\"\$TEMPORAL_API_KEY\" namespace search-attributes add --namespace=\"$namespace\" --sa=\"$attr\""
        continue
      fi

      if ! tcld --api-key="$TEMP_API_KEY" namespace search-attributes add \
            --namespace "$namespace" \
            --sa "$attr"; then
        echo "[$namespace] Failed to add $attr" >&2
        ((failures++))
      fi
    done
  done

  echo
  if (( failures > 0 )); then
    abort "Completed with $failures error(s)." "$failures"
  fi

  echo "All done."
}

main "$@"
