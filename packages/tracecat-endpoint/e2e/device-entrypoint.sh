#!/usr/bin/env bash

set -euo pipefail

: "${TRACECAT_SERVER_URL:?TRACECAT_SERVER_URL is required}"
: "${TRACECAT_ENDPOINT_ID:?TRACECAT_ENDPOINT_ID is required}"
: "${TRACECAT_ENROLLMENT_TOKEN:?TRACECAT_ENROLLMENT_TOKEN is required}"
: "${TRACECAT_DEVICE_NAME:?TRACECAT_DEVICE_NAME is required}"
: "${TRACECAT_PREVIEW_SCENARIO:?TRACECAT_PREVIEW_SCENARIO is required}"

exec /usr/local/bin/tracecatd run \
  --server-url "${TRACECAT_SERVER_URL}" \
  --state-dir /state \
  --home-dir /home/tracecat \
  --endpoint-id "${TRACECAT_ENDPOINT_ID}" \
  --enrollment-token "${TRACECAT_ENROLLMENT_TOKEN}" \
  --interval "${TRACECAT_SYNC_INTERVAL:-5s}"
