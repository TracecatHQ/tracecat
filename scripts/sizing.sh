#!/usr/bin/env bash
# Tracecat sizing calculator for Docker Compose deployments.
#
# Given a target workflow throughput and a rough workload profile, prints the
# executor and worker replica counts, the concurrency environment variables,
# and the PostgreSQL max_connections the stack needs, then checks whether the
# host (or the CPU/memory you pass in) can support it.
#
# The model is documented at https://docs.tracecat.com/self-hosting/scaling.
# All constants below are calibration points, not hard limits. Override any of
# them through the environment, e.g. EXECUTOR_SLOTS_PER_VCPU=6 ./sizing.sh ...

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: sizing.sh [options]

Workload:
  -r, --workflows-per-sec N     Target workflow starts per second (default: 1)
  -a, --actions-per-workflow N  Average actions per workflow run (default: 5)
  -t, --action-seconds N        Average wall-clock seconds per action (default: 2)
      --headroom N              Capacity multiplier over steady state (default: 1.5)

Hardware (auto-detected from this machine when omitted):
  -c, --cpus N                  CPU cores available to Docker
  -m, --memory-gb N             Memory in GiB available to Docker

Shape (optional):
      --executor-vcpu N         CPUs per executor replica (default: 4)
      --worker-vcpu N           CPUs per worker replica (default: 2)

  -h, --help                    Show this help

Examples:
  ./scripts/sizing.sh --workflows-per-sec 5 --actions-per-workflow 8 --action-seconds 3
  ./scripts/sizing.sh -r 20 -c 32 -m 64
EOF
}

# --- Calibration constants (override via environment) ------------------------
# Executor: one subprocess per running action (direct backend).
EXECUTOR_SLOTS_PER_VCPU=${EXECUTOR_SLOTS_PER_VCPU:-4}
EXECUTOR_MEM_MB_PER_SLOT=${EXECUTOR_MEM_MB_PER_SLOT:-512}
EXECUTOR_BASE_MEM_MB=${EXECUTOR_BASE_MEM_MB:-1024}
# Worker: workflow starts per second one replica sustains at WORKER_VCPU cores.
WORKER_STARTS_PER_SEC_PER_REPLICA=${WORKER_STARTS_PER_SEC_PER_REPLICA:-10}
WORKER_MEM_MB=${WORKER_MEM_MB:-2048}
# Everything else in the stack: caddy, ui, api, postgres, temporal + its db,
# redis, minio, agent-worker, agent-executor, mcp, litellm.
BASE_STACK_CPU=${BASE_STACK_CPU:-4}
BASE_STACK_MEM_GB=${BASE_STACK_MEM_GB:-10}
# PostgreSQL connections per Tracecat process (see docs for the derivation).
DB_POOL_SIZE=${TRACECAT__DB_POOL_SIZE:-10}
DB_MAX_OVERFLOW=${TRACECAT__DB_MAX_OVERFLOW:-60}
DB_AUTH_POOL_SIZE=${TRACECAT__DB_AUTH_POOL_SIZE:-5}
DB_AUTH_MAX_OVERFLOW=${TRACECAT__DB_AUTH_MAX_OVERFLOW:-5}
# Processes that hold a pool but are not sized by this script.
FIXED_DB_CLIENTS=${FIXED_DB_CLIENTS:-5} # api, agent-worker, agent-executor, mcp, litellm
PG_RESERVED_CONNECTIONS=${PG_RESERVED_CONNECTIONS:-20}
PG_MEM_MB_PER_CONNECTION=${PG_MEM_MB_PER_CONNECTION:-10}

# --- Defaults ----------------------------------------------------------------
WORKFLOWS_PER_SEC=1
ACTIONS_PER_WORKFLOW=5
ACTION_SECONDS=2
HEADROOM=1.5
EXECUTOR_VCPU=4
WORKER_VCPU=2
CPUS=""
MEMORY_GB=""

while [ $# -gt 0 ]; do
  case "$1" in
    -r|--workflows-per-sec) WORKFLOWS_PER_SEC=$2; shift 2 ;;
    -a|--actions-per-workflow) ACTIONS_PER_WORKFLOW=$2; shift 2 ;;
    -t|--action-seconds) ACTION_SECONDS=$2; shift 2 ;;
    --headroom) HEADROOM=$2; shift 2 ;;
    -c|--cpus) CPUS=$2; shift 2 ;;
    -m|--memory-gb) MEMORY_GB=$2; shift 2 ;;
    --executor-vcpu) EXECUTOR_VCPU=$2; shift 2 ;;
    --worker-vcpu) WORKER_VCPU=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

is_number() { printf '%s' "$1" | grep -Eq '^[0-9]+([.][0-9]+)?$'; }
for v in WORKFLOWS_PER_SEC ACTIONS_PER_WORKFLOW ACTION_SECONDS HEADROOM EXECUTOR_VCPU WORKER_VCPU; do
  is_number "${!v}" || { echo "$v must be a positive number, got '${!v}'" >&2; exit 2; }
done

# --- Hardware detection ------------------------------------------------------
detect_cpus() {
  if command -v nproc >/dev/null 2>&1; then nproc
  elif command -v sysctl >/dev/null 2>&1; then sysctl -n hw.ncpu
  else echo 0
  fi
}
detect_memory_gb() {
  if [ -r /proc/meminfo ]; then
    awk '/MemTotal/ {printf "%.1f", $2 / 1024 / 1024}' /proc/meminfo
  elif command -v sysctl >/dev/null 2>&1; then
    awk -v b="$(sysctl -n hw.memsize)" 'BEGIN {printf "%.1f", b / 1024 / 1024 / 1024}'
  else echo 0
  fi
}
HW_SOURCE="provided"
if [ -z "$CPUS" ] || [ -z "$MEMORY_GB" ]; then
  HW_SOURCE="detected on this host"
  [ -z "$CPUS" ] && CPUS=$(detect_cpus)
  [ -z "$MEMORY_GB" ] && MEMORY_GB=$(detect_memory_gb)
fi
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  DOCKER_CPUS=$(docker info --format '{{.NCPU}}' 2>/dev/null || echo "")
  DOCKER_MEM_GB=$(docker info --format '{{.MemTotal}}' 2>/dev/null | awk '{printf "%.1f", $1 / 1024 / 1024 / 1024}')
  if [ -n "$DOCKER_CPUS" ] && [ "$HW_SOURCE" != "provided" ]; then
    CPUS=$DOCKER_CPUS
    MEMORY_GB=$DOCKER_MEM_GB
    HW_SOURCE="reported by docker info"
  fi
fi

# --- Model -------------------------------------------------------------------
awk \
  -v R="$WORKFLOWS_PER_SEC" -v A="$ACTIONS_PER_WORKFLOW" -v T="$ACTION_SECONDS" \
  -v H="$HEADROOM" -v CPUS="$CPUS" -v MEM_GB="$MEMORY_GB" -v HW_SOURCE="$HW_SOURCE" \
  -v EXEC_VCPU="$EXECUTOR_VCPU" -v WORKER_VCPU="$WORKER_VCPU" \
  -v SLOTS_PER_VCPU="$EXECUTOR_SLOTS_PER_VCPU" -v MEM_PER_SLOT="$EXECUTOR_MEM_MB_PER_SLOT" \
  -v EXEC_BASE_MEM="$EXECUTOR_BASE_MEM_MB" \
  -v WORKER_RATE="$WORKER_STARTS_PER_SEC_PER_REPLICA" -v WORKER_MEM="$WORKER_MEM_MB" \
  -v BASE_CPU="$BASE_STACK_CPU" -v BASE_MEM_GB="$BASE_STACK_MEM_GB" \
  -v POOL="$DB_POOL_SIZE" -v OVERFLOW="$DB_MAX_OVERFLOW" \
  -v AUTH_POOL="$DB_AUTH_POOL_SIZE" -v AUTH_OVERFLOW="$DB_AUTH_MAX_OVERFLOW" \
  -v FIXED_CLIENTS="$FIXED_DB_CLIENTS" -v PG_RESERVED="$PG_RESERVED_CONNECTIONS" \
  -v PG_MEM_PER_CONN="$PG_MEM_MB_PER_CONNECTION" \
'
function ceil(x) { return (x == int(x)) ? x : int(x) + 1 }
function max(a, b) { return a > b ? a : b }

# Size the stack for a workflow start rate r. Returns via globals.
function size(r) {
  actions_per_sec = r * A
  inflight = actions_per_sec * T * H
  slots_needed = max(4, ceil(inflight))
  max_slots_per_exec = EXEC_VCPU * SLOTS_PER_VCPU
  exec_replicas = ceil(slots_needed / max_slots_per_exec)
  slots_per_exec = ceil(slots_needed / exec_replicas)
  exec_slots = exec_replicas * slots_per_exec
  exec_cpu = ceil(exec_slots / SLOTS_PER_VCPU)
  exec_mem_gb = (exec_replicas * EXEC_BASE_MEM + exec_slots * MEM_PER_SLOT) / 1024

  worker_scale = WORKER_VCPU / 2
  worker_replicas = max(1, ceil(r * H / (WORKER_RATE * worker_scale)))
  inflight_workflows = ceil(r * A * T * H)
  workflow_tasks = max(100, ceil(inflight_workflows / worker_replicas))
  worker_cpu = worker_replicas * WORKER_VCPU
  worker_mem_gb = worker_replicas * WORKER_MEM / 1024

  per_process = POOL + OVERFLOW + AUTH_POOL + AUTH_OVERFLOW
  db_clients = FIXED_CLIENTS + worker_replicas + exec_replicas
  max_connections = db_clients * per_process + PG_RESERVED
  pg_mem_gb = max_connections * PG_MEM_PER_CONN / 1024

  total_cpu = BASE_CPU + exec_cpu + worker_cpu
  total_mem_gb = BASE_MEM_GB + exec_mem_gb + worker_mem_gb
}

BEGIN {
  size(R)
  printf "Tracecat sizing estimate\n"
  printf "========================\n\n"
  printf "Workload\n"
  printf "  Workflow starts/sec        %.2f\n", R
  printf "  Actions per workflow       %d\n", A
  printf "  Seconds per action         %.1f\n", T
  printf "  Headroom                   %.2fx\n", H
  printf "  Actions/sec                %.1f\n", actions_per_sec
  printf "  Actions in flight          %d  (actions/sec x seconds/action x headroom)\n\n", ceil(inflight)

  printf "Executors\n"
  printf "  Replicas                   %d\n", exec_replicas
  printf "  Slots per replica          %d  (%d per vCPU, max %d per %d-vCPU replica)\n", slots_per_exec, SLOTS_PER_VCPU, max_slots_per_exec, EXEC_VCPU
  printf "  Total slots                %d\n", exec_slots
  printf "  CPU                        %d\n", exec_cpu
  printf "  Memory                     %.1f GiB\n\n", exec_mem_gb

  printf "Workers\n"
  printf "  Replicas                   %d x %d vCPU\n", worker_replicas, WORKER_VCPU
  printf "  Workflows in flight        %d\n", inflight_workflows
  printf "  Memory                     %.1f GiB\n\n", worker_mem_gb

  printf "PostgreSQL\n"
  printf "  Pooled clients             %d processes x up to %d connections each\n", db_clients, per_process
  printf "  max_connections            %d  (compose default is 100)\n", max_connections
  printf "  Memory if all in use       %.1f GiB  (worst case, excluded from the check below)\n\n", pg_mem_gb

  printf "Hardware check (%s: %s CPUs, %s GiB)\n", HW_SOURCE, CPUS, MEM_GB
  printf "  Required                   %d CPUs, %.1f GiB\n", total_cpu, total_mem_gb
  cpu_ok = (CPUS + 0 >= total_cpu)
  mem_ok = (MEM_GB + 0 >= total_mem_gb)
  if (CPUS + 0 == 0 || MEM_GB + 0 == 0) {
    printf "  Result                     UNKNOWN (pass --cpus and --memory-gb)\n"
  } else if (cpu_ok && mem_ok) {
    printf "  Result                     OK\n"
  } else {
    printf "  Result                     INSUFFICIENT"
    if (!cpu_ok) printf " (CPU short by %d)", total_cpu - CPUS
    if (!mem_ok) printf " (memory short by %.1f GiB)", total_mem_gb - MEM_GB
    printf "\n"
    # Find the largest rate this hardware supports.
    lo = 0; hi = R
    for (i = 0; i < 40; i++) {
      mid = (lo + hi) / 2
      size(mid)
      if (CPUS + 0 >= total_cpu && MEM_GB + 0 >= total_mem_gb) lo = mid; else hi = mid
    }
    size(lo)
    if (lo < 0.01) {
      printf "  Max sustainable rate       below the minimum stack footprint (%d CPUs, %.1f GiB)\n", total_cpu, total_mem_gb
    } else {
      printf "  Max sustainable rate       ~%.2f workflow starts/sec on this hardware\n", lo
      printf "                             (%d executor(s) x %d slots, %d worker(s))\n", exec_replicas, slots_per_exec, worker_replicas
    }
    size(R)
  }

  printf "\nApply\n"
  printf "-----\n"
  printf "# .env\n"
  printf "TRACECAT__EXECUTOR_MAX_CONCURRENT_ACTIVITIES=%d\n", slots_per_exec
  printf "TRACECAT__EXECUTOR_THREADPOOL_MAX_WORKERS=%d\n", slots_per_exec
  printf "TEMPORAL__MAX_CONCURRENT_WORKFLOW_TASKS=%d\n", workflow_tasks
  printf "TEMPORAL__MAX_CONCURRENT_ACTIVITIES=%d\n", workflow_tasks
  printf "TEMPORAL__THREADPOOL_MAX_WORKERS=%d\n", workflow_tasks
  printf "TRACECAT__DB_POOL_SIZE=%d\n", POOL
  printf "TRACECAT__DB_MAX_OVERFLOW=%d\n\n", OVERFLOW
  printf "# docker-compose.override.yml (postgres_db)\n"
  printf "services:\n  postgres_db:\n    command: [\"postgres\", \"-c\", \"max_connections=%d\"]\n\n", max_connections
  printf "# Start the stack\n"
  printf "docker compose up -d --scale executor=%d --scale worker=%d\n", exec_replicas, worker_replicas
}
'
