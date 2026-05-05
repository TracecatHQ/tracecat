#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${PACKAGE_DIR}/../.." && pwd)"

COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
COMPOSE_PROJECT_NAME="tracecat-preview-devices"
SCENARIO_DIR="${SCRIPT_DIR}/scenarios"
DIST_DIR="${PACKAGE_DIR}/dist/preview-devices"
SESSION_DIR="${DIST_DIR}/session"
COOKIE_JAR="${SESSION_DIR}/cookies.txt"
TRACECATD_BINARY="${PACKAGE_DIR}/dist/tracecatd"
DEV_USER_EMAIL="${TRACECAT__DEV_USER_EMAIL:-dev@tracecat.com}"
DEV_USER_PASSWORD="${TRACECAT__DEV_USER_PASSWORD:-password1234}"

readonly SCENARIOS=("baseline" "rogue_mcp" "rogue_instruction_file")

usage() {
  cat <<'EOF'
Usage:
  ./packages/tracecat-endpoint/e2e/preview-devices.sh [--cluster <n>] [--server-url <origin>] up [-d] [compose args...]
  ./packages/tracecat-endpoint/e2e/preview-devices.sh down [compose args...]
  ./packages/tracecat-endpoint/e2e/preview-devices.sh ps [compose args...]
  ./packages/tracecat-endpoint/e2e/preview-devices.sh logs [compose args...]

Commands:
  up      Build tracecatd, materialize runtime homes, create/reuse enrollments, and start the preview stack.
  down    Stop the preview stack without deleting endpoint state.
  ps      Show preview container status.
  logs    Show preview container logs.

Options:
  --cluster <n>      Tracecat cluster number to target when multiple worktree clusters are running.
  --server-url <url> Override the host app origin used for bootstrap requests and tracecatd.
EOF
}

log() {
  printf '%s\n' "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_tool() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required tool: $1"
}

trim_trailing_slash() {
  printf '%s\n' "${1%/}"
}

get_worktree_id() {
  local git_dir git_common_dir branch

  git_dir=$(git -C "${REPO_ROOT}" rev-parse --git-dir 2>/dev/null) || {
    printf 'main\n'
    return
  }
  git_common_dir=$(git -C "${REPO_ROOT}" rev-parse --git-common-dir 2>/dev/null) || {
    printf 'main\n'
    return
  }

  git_dir=$(cd "${REPO_ROOT}" && cd "${git_dir}" && pwd)
  git_common_dir=$(cd "${REPO_ROOT}" && cd "${git_common_dir}" && pwd)

  if [[ "${git_dir}" == "${git_common_dir}" ]]; then
    printf 'main\n'
    return
  fi

  branch=$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null) || branch="unknown"
  printf '%s\n' "${branch}" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g; s/--*/-/g; s/^-//; s/-$//'
}

running_clusters() {
  local worktree_id=$1
  local prefix="tracecat-${worktree_id}-"

  docker compose ls --format json 2>/dev/null | jq -r '.[].Name' 2>/dev/null | while read -r project; do
    if [[ "${project}" == "${prefix}"* ]]; then
      local num="${project#${prefix}}"
      if [[ "${num}" =~ ^[0-9]+$ ]]; then
        printf '%s\n' "${num}"
      fi
    fi
  done | sort -n
}

resolve_cluster() {
  local worktree_id=$1
  local requested_cluster=$2
  local clusters item found=false cluster_count cluster_list

  clusters=$(running_clusters "${worktree_id}")

  if [[ -n "${requested_cluster}" ]]; then
    while IFS= read -r item; do
      if [[ -n "${item}" && "${item}" == "${requested_cluster}" ]]; then
        found=true
        break
      fi
    done <<< "${clusters}"
    if [[ "${found}" != "true" ]]; then
      fail "Cluster ${requested_cluster} is not running for worktree ${worktree_id}. Run 'just cluster up -d' first."
    fi
    printf '%s\n' "${requested_cluster}"
    return
  fi

  if [[ -z "${clusters}" ]]; then
    fail "No Tracecat cluster is running for this worktree. Run 'just cluster up -d' first."
  fi

  cluster_count=$(printf '%s\n' "${clusters}" | sed '/^$/d' | wc -l | tr -d ' ')
  if [[ "${cluster_count}" == "1" ]]; then
    printf '%s\n' "${clusters}" | sed -n '/./{p;q;}'
    return
  fi

  cluster_list=$(printf '%s\n' "${clusters}" | sed '/^$/d' | tr '\n' ' ' | sed 's/[[:space:]]*$//')
  fail "Multiple Tracecat clusters are running for this worktree (${cluster_list}). Re-run with --cluster <n>."
}

parse_public_origin() {
  local cluster_num=$1
  local ports_output ui_url

  ports_output=$(cd "${REPO_ROOT}" && ./scripts/cluster "${cluster_num}" ports)
  ui_url=$(printf '%s\n' "${ports_output}" | awk '/UI \(Caddy\):/ {print $3}')
  [[ -n "${ui_url}" ]] || fail "Could not parse UI (Caddy) origin from ./scripts/cluster ${cluster_num} ports"
  printf '%s\n' "${ui_url}"
}

parse_api_origin() {
  local cluster_num=$1
  local ports_output api_port

  ports_output=$(cd "${REPO_ROOT}" && ./scripts/cluster "${cluster_num}" ports)
  api_port=$(printf '%s\n' "${ports_output}" | sed -nE 's/.*API:.*\(internal: ([0-9]+)\).*/\1/p')
  [[ -n "${api_port}" ]] || fail "Could not parse API port from ./scripts/cluster ${cluster_num} ports"
  printf 'http://localhost:%s\n' "${api_port}"
}

wait_for_api_ready() {
  local attempts=60
  local status

  while (( attempts > 0 )); do
    status=$(curl -s -o /dev/null -w "%{http_code}" "${API_BASE}/ready" 2>/dev/null || printf '000')
    if [[ "${status}" == "200" ]]; then
      return
    fi
    sleep 2
    attempts=$((attempts - 1))
  done

  fail "Tracecat API at ${API_BASE} did not become ready in time"
}

container_server_url() {
  local origin=$1

  if [[ "${origin}" =~ ^(https?://)(localhost|127\.0\.0\.1)(:[0-9]+)?$ ]]; then
    printf '%shost.docker.internal%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[3]}"
    return
  fi

  printf '%s\n' "${origin}"
}

scenario_name() {
  case "$1" in
    baseline) printf 'Preview Baseline\n' ;;
    rogue_mcp) printf 'Preview Rogue MCP\n' ;;
    rogue_instruction_file) printf 'Preview Rogue Instruction File\n' ;;
    *) fail "Unknown scenario: $1" ;;
  esac
}

scenario_service() {
  case "$1" in
    baseline) printf 'preview-baseline\n' ;;
    rogue_mcp) printf 'preview-rogue-mcp\n' ;;
    rogue_instruction_file) printf 'preview-rogue-instruction-file\n' ;;
    *) fail "Unknown scenario: $1" ;;
  esac
}

scenario_workspace_dir() {
  case "$1" in
    baseline) printf 'workspace-baseline\n' ;;
    rogue_mcp) printf 'workspace-rogue-mcp\n' ;;
    rogue_instruction_file) printf 'workspace-rogue-instruction-file\n' ;;
    *) fail "Unknown scenario: $1" ;;
  esac
}

materialize_home() {
  local scenario=$1
  local source_dir="${SCENARIO_DIR}/${scenario}/home"
  local runtime_dir="${DIST_DIR}/${scenario}/home"
  local runtime_workspace
  runtime_workspace=$(scenario_workspace_dir "${scenario}")

  [[ -d "${source_dir}" ]] || fail "Missing tracked scenario template: ${source_dir}"

  rm -rf "${runtime_dir}"
  mkdir -p "${runtime_dir}"
  cp -R "${source_dir}/." "${runtime_dir}/"

  if [[ -d "${runtime_dir}/workspace-alpha" && "${runtime_workspace}" != "workspace-alpha" ]]; then
    mv "${runtime_dir}/workspace-alpha" "${runtime_dir}/${runtime_workspace}"
  fi

  while IFS= read -r -d '' file; do
    local tmp
    tmp=$(mktemp)
    sed \
      -e 's#__HOME__#/home/tracecat#g' \
      -e "s#workspace-alpha#${runtime_workspace}#g" \
      "${file}" > "${tmp}"
    mv "${tmp}" "${file}"
  done < <(find "${runtime_dir}" -type f -print0)
}

ensure_session_dir() {
  mkdir -p "${SESSION_DIR}"
  : > "${COOKIE_JAR}"
}

has_existing_enrollments() {
  local scenario scenario_dir

  for scenario in "${SCENARIOS[@]}"; do
    scenario_dir="${DIST_DIR}/${scenario}"
    if [[ ! -f "${scenario_dir}/state/state.json" && ! -f "${scenario_dir}/device.env" ]]; then
      return 1
    fi
  done
}

curl_status() {
  local output_file=$1
  shift
  curl -sS -o "${output_file}" -w "%{http_code}" "$@"
}

bootstrap_auth() {
  local response_file status org_file
  response_file=$(mktemp)
  org_file=$(mktemp)

  status=$(curl_status "${response_file}" \
    -c "${COOKIE_JAR}" \
    -b "${COOKIE_JAR}" \
    -X POST "${API_BASE}/auth/login" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "username=${DEV_USER_EMAIL}" \
    --data-urlencode "password=${DEV_USER_PASSWORD}")

  [[ "${status}" == "204" ]] || fail "Login failed for ${DEV_USER_EMAIL} with status ${status}: $(cat "${response_file}")"

  status=$(curl_status "${org_file}" \
    -c "${COOKIE_JAR}" \
    -b "${COOKIE_JAR}" \
    "${API_BASE}/organization")

  [[ "${status}" == "200" ]] || fail "Fetching current organization failed with status ${status}: $(cat "${org_file}")"
  ORG_ID=$(jq -r '.id // empty' "${org_file}")
  [[ -n "${ORG_ID}" ]] || fail "No organization id found from ${API_BASE}/organization"
}

org_curl_status() {
  local output_file=$1
  shift
  curl_status "${output_file}" \
    -c "${COOKIE_JAR}" \
    -b "${COOKIE_JAR}" \
    -b "tracecat-org-id=${ORG_ID}" \
    "$@"
}

write_device_env() {
  local scenario=$1
  local endpoint_id=$2
  local token=$3
  local device_name
  device_name=$(scenario_name "${scenario}")

  cat > "${DIST_DIR}/${scenario}/device.env" <<EOF
TRACECAT_ENDPOINT_ID=${endpoint_id}
TRACECAT_ENROLLMENT_TOKEN=${token}
TRACECAT_DEVICE_NAME=${device_name}
TRACECAT_PREVIEW_SCENARIO=${scenario}
TRACECAT_PREVIEW_STACK=${COMPOSE_PROJECT_NAME}
EOF
}

ensure_scenario_enrollment() {
  local scenario=$1
  local scenario_dir="${DIST_DIR}/${scenario}"
  local state_file="${scenario_dir}/state/state.json"
  local env_file="${scenario_dir}/device.env"
  local response_file endpoint_id token name status current_server_url tmp_state

  mkdir -p "${scenario_dir}/state"
  name=$(scenario_name "${scenario}")

  if [[ -f "${state_file}" ]]; then
    current_server_url=$(jq -r '.server_url // empty' "${state_file}")
    if [[ "${current_server_url}" != "${TRACECAT_CONTAINER_SERVER_URL}" ]]; then
      tmp_state=$(mktemp)
      jq --arg server_url "${TRACECAT_CONTAINER_SERVER_URL}" '.server_url = $server_url' "${state_file}" > "${tmp_state}"
      mv "${tmp_state}" "${state_file}"
    fi

    if [[ ! -f "${env_file}" ]]; then
      endpoint_id=$(jq -r '.endpoint_id // empty' "${state_file}")
      token=$(jq -r '.token // empty' "${state_file}")
      [[ -n "${endpoint_id}" && -n "${token}" ]] || fail "State file exists for ${scenario}, but ${state_file} is missing endpoint_id/token"
      write_device_env "${scenario}" "${endpoint_id}" "${token}"
    fi

    endpoint_id=$(grep '^TRACECAT_ENDPOINT_ID=' "${env_file}" | cut -d= -f2-)
    log "${scenario}: reusing enrollment (${endpoint_id})"
    return
  fi

  if [[ -f "${env_file}" ]]; then
    endpoint_id=$(grep '^TRACECAT_ENDPOINT_ID=' "${env_file}" | cut -d= -f2-)
    token=$(grep '^TRACECAT_ENROLLMENT_TOKEN=' "${env_file}" | cut -d= -f2-)
    [[ -n "${endpoint_id}" && -n "${token}" ]] || fail "Env file exists for ${scenario}, but ${env_file} is missing endpoint_id/token"
    log "${scenario}: reusing enrollment (${endpoint_id})"
    return
  fi

  response_file=$(mktemp)
  status=$(org_curl_status "${response_file}" \
    -X POST "${API_BASE}/spm/endpoints" \
    -H "Content-Type: application/json" \
    --data "$(jq -nc \
      --arg name "${name}" \
      --arg scenario "${scenario}" \
      --arg stack "${COMPOSE_PROJECT_NAME}" \
      '{name:$name,harness:"claude_code",platform:"macos",client_metadata:{preview_stack:$stack,preview_scenario:$scenario}}')")

  [[ "${status}" == "201" ]] || fail "Endpoint create failed for ${scenario} with status ${status}: $(cat "${response_file}")"

  endpoint_id=$(jq -r '.endpoint.id // empty' "${response_file}")
  token=$(jq -r '.enrollment_token // empty' "${response_file}")
  [[ -n "${endpoint_id}" && -n "${token}" ]] || fail "Endpoint create response for ${scenario} is missing endpoint.id or enrollment_token"

  write_device_env "${scenario}" "${endpoint_id}" "${token}"
  log "${scenario}: created enrollment (${endpoint_id})"
}

build_tracecatd() {
  local goarch
  goarch=$(cd "${PACKAGE_DIR}" && go env GOARCH)
  log "Building Linux ${goarch} binary at ${TRACECATD_BINARY}"
  (cd "${PACKAGE_DIR}" && CGO_ENABLED=0 GOOS=linux GOARCH="${goarch}" go build -o dist/tracecatd ./cmd/tracecatd)
}

compose() {
  TRACECAT_CONTAINER_SERVER_URL="${TRACECAT_CONTAINER_SERVER_URL:-http://host.docker.internal}" \
    docker compose -f "${COMPOSE_FILE}" -p "${COMPOSE_PROJECT_NAME}" "$@"
}

wait_for_containers() {
  local attempts=60
  local services_json service_name scenario

  while (( attempts > 0 )); do
    services_json=$(compose ps --format json 2>/dev/null | jq -s '.' 2>/dev/null || true)
    if [[ -n "${services_json}" ]]; then
      local running_count=0
      for scenario in "${SCENARIOS[@]}"; do
        service_name=$(scenario_service "${scenario}")
        if jq -e --arg svc "${service_name}" '.[] | select(.Service == $svc and .State == "running")' >/dev/null 2>&1 <<<"${services_json}"; then
          running_count=$((running_count + 1))
        fi
      done
      if [[ "${running_count}" -eq "${#SCENARIOS[@]}" ]]; then
        return
      fi
    fi
    sleep 2
    attempts=$((attempts - 1))
  done

  fail "Preview containers did not all reach the running state in time"
}

wait_for_endpoints() {
  local endpoints_url="${API_BASE}/spm/endpoints?limit=100"
  local attempts=90
  local response_file status scenario device_name

  response_file=$(mktemp)
  while (( attempts > 0 )); do
    status=$(org_curl_status "${response_file}" "${endpoints_url}")
    if [[ "${status}" == "200" ]]; then
      local found=0
      for scenario in "${SCENARIOS[@]}"; do
        device_name=$(scenario_name "${scenario}")
        if jq -e --arg name "${device_name}" '.items[]? | select(.name == $name)' "${response_file}" >/dev/null 2>&1; then
          found=$((found + 1))
        fi
      done
      if [[ "${found}" -eq "${#SCENARIOS[@]}" ]]; then
        return
      fi
    fi
    sleep 2
    attempts=$((attempts - 1))
  done

  fail "Preview endpoints did not all appear in ${endpoints_url} before timeout"
}

ensure_detached_args() {
  local arg
  for arg in "$@"; do
    if [[ "${arg}" == "-d" || "${arg}" == "--detach" ]]; then
      return
    fi
  done
  EXTRA_ARGS+=("-d")
}

print_summary() {
  local scenario env_file endpoint_id

  log "Selected cluster: ${CLUSTER_NUM}"
  log "Host app origin: ${APP_ORIGIN}"
  log "Container server URL: ${TRACECAT_CONTAINER_SERVER_URL}"
  log "Binary build path: ${TRACECATD_BINARY}"
  for scenario in "${SCENARIOS[@]}"; do
    env_file="${DIST_DIR}/${scenario}/device.env"
    endpoint_id=$(grep '^TRACECAT_ENDPOINT_ID=' "${env_file}" | cut -d= -f2-)
    log "${scenario}: endpoint ${endpoint_id}"
  done
  log "SPM UI: ${APP_ORIGIN}/spm/endpoints"
}

COMMAND=""
CLUSTER_NUM=""
SERVER_URL_OVERRIDE=""
AUTH_BOOTSTRAPPED=false
declare -a EXTRA_ARGS=()
EXTRA_ARGS_COUNT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cluster)
      [[ $# -ge 2 ]] || fail "--cluster requires a value"
      CLUSTER_NUM=$2
      shift 2
      ;;
    --server-url)
      [[ $# -ge 2 ]] || fail "--server-url requires a value"
      SERVER_URL_OVERRIDE=$(trim_trailing_slash "$2")
      shift 2
      ;;
    up|down|ps|logs)
      COMMAND=$1
      shift
      EXTRA_ARGS=("$@")
      EXTRA_ARGS_COUNT=$#
      break
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown argument: $1"
      ;;
  esac
done

[[ -n "${COMMAND}" ]] || {
  usage
  exit 1
}

case "${COMMAND}" in
  up)
    require_tool docker
    require_tool curl
    require_tool jq
    require_tool go

    if [[ -n "${SERVER_URL_OVERRIDE}" ]]; then
      CLUSTER_NUM="server-url-override"
      APP_ORIGIN="${SERVER_URL_OVERRIDE}"
      API_BASE="${SERVER_URL_OVERRIDE}"
    else
      WORKTREE_ID=$(get_worktree_id)
      CLUSTER_NUM=$(resolve_cluster "${WORKTREE_ID}" "${CLUSTER_NUM}")

      DEFAULT_APP_ORIGIN=$(parse_public_origin "${CLUSTER_NUM}")
      DEFAULT_API_BASE=$(parse_api_origin "${CLUSTER_NUM}")
      APP_ORIGIN="${DEFAULT_APP_ORIGIN}"
      API_BASE="${DEFAULT_API_BASE}"
    fi
    TRACECAT_CONTAINER_SERVER_URL=$(container_server_url "${API_BASE}")

    mkdir -p "${DIST_DIR}"
    ensure_session_dir
    wait_for_api_ready
    if ! has_existing_enrollments; then
      bootstrap_auth
      AUTH_BOOTSTRAPPED=true
    fi
    build_tracecatd

    for scenario in "${SCENARIOS[@]}"; do
      materialize_home "${scenario}"
      ensure_scenario_enrollment "${scenario}"
    done

    print_summary

    export TRACECAT_CONTAINER_SERVER_URL
    if [[ "${EXTRA_ARGS_COUNT}" -gt 0 ]]; then
      ensure_detached_args "${EXTRA_ARGS[@]}"
      compose up --build "${EXTRA_ARGS[@]}"
    else
      ensure_detached_args
      compose up --build
    fi

    wait_for_containers
    if [[ "${AUTH_BOOTSTRAPPED}" == "true" ]]; then
      wait_for_endpoints
    fi
    ;;
  down)
    if [[ "${EXTRA_ARGS_COUNT}" -gt 0 ]]; then
      for arg in "${EXTRA_ARGS[@]}"; do
        if [[ "${arg}" == "-v" || "${arg}" == "--volumes" ]]; then
          fail "Volume removal is out of scope for preview device teardown. Re-run without ${arg}."
        fi
      done
      compose down --remove-orphans "${EXTRA_ARGS[@]}"
    else
      compose down --remove-orphans
    fi
    ;;
  ps)
    if [[ "${EXTRA_ARGS_COUNT}" -gt 0 ]]; then
      compose ps "${EXTRA_ARGS[@]}"
    else
      compose ps
    fi
    ;;
  logs)
    if [[ "${EXTRA_ARGS_COUNT}" -gt 0 ]]; then
      compose logs "${EXTRA_ARGS[@]}"
    else
      compose logs
    fi
    ;;
  *)
    fail "Unsupported command: ${COMMAND}"
    ;;
esac
