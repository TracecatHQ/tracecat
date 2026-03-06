#!/usr/bin/env bash
set -euo pipefail

ready_file="${TRACECAT__EXECUTOR_WARM_READY_FILE:-/tmp/tracecat/executor-warm.ready}"
rm -f "$ready_file"

exec python -m tracecat.executor.worker
