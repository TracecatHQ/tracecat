#!/usr/bin/env bash

set -euo pipefail

export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FARGATE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Error: required command not found: ${command_name}" >&2
    exit 1
  fi
}

terraform_output_raw() {
  local output_name="$1"
  terraform output -raw "$output_name"
}

terraform_output_json() {
  local output_name="$1"
  terraform output -json "$output_name"
}

require_command aws
require_command jq
require_command terraform

cd "$FARGATE_DIR"

cluster_name="$(terraform_output_raw ecs_cluster_name)"
task_definition_arn="$(terraform_output_raw tracecat_migrations_task_definition_arn)"
container_name="$(terraform_output_raw tracecat_migrations_container_name)"
private_subnet_ids="$(terraform_output_json private_subnet_ids)"
security_group_ids="$(terraform_output_json tracecat_migrations_security_group_ids)"

network_configuration="$(
  jq -cn \
    --argjson subnets "$private_subnet_ids" \
    --argjson securityGroups "$security_group_ids" \
    '{
      awsvpcConfiguration: {
        subnets: $subnets,
        securityGroups: $securityGroups,
        assignPublicIp: "DISABLED"
      }
    }'
)"

echo "Starting Tracecat migrations task ${task_definition_arn} on cluster ${cluster_name}"

run_task_response="$(
  aws ecs run-task \
    --cluster "$cluster_name" \
    --task-definition "$task_definition_arn" \
    --launch-type FARGATE \
    --network-configuration "$network_configuration" \
    --started-by "tracecat-migrations-$(date +%s)" \
    --count 1 \
    --output json
)"

run_task_failures="$(jq -r '.failures | length' <<<"$run_task_response")"
if [[ "$run_task_failures" != "0" ]]; then
  echo "Error: failed to start migrations task:" >&2
  jq -r '.failures[] | "- " + (.arn // "unknown") + ": " + (.reason // "unknown") + " " + (.detail // "")' \
    <<<"$run_task_response" >&2
  exit 1
fi

task_arn="$(jq -r '.tasks[0].taskArn // empty' <<<"$run_task_response")"
if [[ -z "$task_arn" ]]; then
  echo "Error: ECS did not return a task ARN for the migrations task" >&2
  exit 1
fi

echo "Waiting for migrations task to stop: ${task_arn}"
aws ecs wait tasks-stopped --cluster "$cluster_name" --tasks "$task_arn"

describe_response="$(
  aws ecs describe-tasks \
    --cluster "$cluster_name" \
    --tasks "$task_arn" \
    --output json
)"

describe_failures="$(jq -r '.failures | length' <<<"$describe_response")"
if [[ "$describe_failures" != "0" ]]; then
  echo "Error: failed to describe migrations task:" >&2
  jq -r '.failures[] | "- " + (.arn // "unknown") + ": " + (.reason // "unknown") + " " + (.detail // "")' \
    <<<"$describe_response" >&2
  exit 1
fi

exit_code="$(
  jq -r \
    --arg container_name "$container_name" \
    '.tasks[0].containers[]? | select(.name == $container_name) | .exitCode // empty' \
    <<<"$describe_response"
)"
stopped_reason="$(jq -r '.tasks[0].stoppedReason // "unknown"' <<<"$describe_response")"
container_reason="$(
  jq -r \
    --arg container_name "$container_name" \
    '.tasks[0].containers[]? | select(.name == $container_name) | .reason // empty' \
    <<<"$describe_response"
)"

if [[ -z "$exit_code" ]]; then
  echo "Error: migrations container did not report an exit code" >&2
  echo "Task stopped reason: ${stopped_reason}" >&2
  if [[ -n "$container_reason" ]]; then
    echo "Container reason: ${container_reason}" >&2
  fi
  exit 1
fi

if [[ "$exit_code" != "0" ]]; then
  echo "Error: migrations task failed with exit code ${exit_code}" >&2
  echo "Task stopped reason: ${stopped_reason}" >&2
  if [[ -n "$container_reason" ]]; then
    echo "Container reason: ${container_reason}" >&2
  fi
  exit "$exit_code"
fi

echo "Tracecat migrations completed successfully"
