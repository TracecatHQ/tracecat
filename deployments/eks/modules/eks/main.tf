# Fetch current AWS region and account
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
data "aws_vpc" "selected" {
  id = var.vpc_id
}

# Validate instance type architecture compatibility for all node groups.
data "aws_ec2_instance_type" "node_group" {
  for_each = toset(concat(
    var.node_instance_types,
    var.spot_node_group_enabled ? var.spot_node_instance_types : []
  ))

  instance_type = each.value
}

# Fetch AWS RDS CA certificate bundle for TLS verification
# https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html
data "http" "rds_ca_bundle" {
  url = "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"
}

locals {
  aws_region     = data.aws_region.current.region
  aws_account_id = data.aws_caller_identity.current.account_id

  # aws_ec2_instance_type uses x86_64 while Kubernetes uses amd64.
  expected_node_architecture = var.node_architecture == "amd64" ? "x86_64" : "arm64"
  on_demand_arch_mismatches = [
    for instance_type in var.node_instance_types : instance_type
    if !contains(data.aws_ec2_instance_type.node_group[instance_type].supported_architectures, local.expected_node_architecture)
  ]
  spot_arch_mismatches = var.spot_node_group_enabled ? [
    for instance_type in var.spot_node_instance_types : instance_type
    if !contains(data.aws_ec2_instance_type.node_group[instance_type].supported_architectures, local.expected_node_architecture)
  ] : []

  # RDS identifier suffix to avoid clashes during snapshot restore
  rds_suffix = random_id.rds_suffix.hex

  # RDS master secret ARN (managed by AWS)
  rds_master_secret_arn = aws_db_instance.tracecat.master_user_secret[0].secret_arn

  # Secrets synced by ESO
  external_secrets_secret_arns = compact([
    var.tracecat_secrets_arn,
    local.rds_master_secret_arn,
    aws_secretsmanager_secret.redis_url.arn,
    var.temporal_secret_arn,
    var.grafana_cloud_credentials_secret_arn,
  ])

  # External Secrets Operator settings
  external_secrets_store_name = "tracecat-aws-secrets"

  # Tracecat service account name for IRSA (matches Helm release defaults)
  tracecat_service_account_name = "tracecat-app"

  # S3 bucket names (using random suffix instead of account ID for security)
  s3_suffix                   = random_id.s3_suffix.hex
  s3_attachments_bucket       = "tracecat-attachments-${local.s3_suffix}"
  s3_registry_bucket          = "tracecat-registry-${local.s3_suffix}"
  s3_workflow_bucket          = "tracecat-workflow-${local.s3_suffix}"
  s3_temporal_archival_bucket = "tracecat-temporal-archival-${local.s3_suffix}"

  # Common labels for Kubernetes resources
  common_labels = {
    "app.kubernetes.io/managed-by" = "terraform"
    "app.kubernetes.io/part-of"    = "tracecat"
  }

  rollout_surge_fraction = var.rollout_surge_percent / 100

  api_rollout_replicas            = var.api_replicas + ceil(var.api_replicas * local.rollout_surge_fraction)
  worker_rollout_replicas         = var.worker_replicas + ceil(var.worker_replicas * local.rollout_surge_fraction)
  executor_rollout_replicas       = var.executor_replicas + ceil(var.executor_replicas * local.rollout_surge_fraction)
  agent_executor_rollout_replicas = var.agent_executor_replicas + ceil(var.agent_executor_replicas * local.rollout_surge_fraction)
  ui_rollout_replicas             = var.ui_replicas + ceil(var.ui_replicas * local.rollout_surge_fraction)

  tracecat_rollout_peak_cpu_millicores = (
    local.api_rollout_replicas * var.api_cpu_request_millicores +
    local.worker_rollout_replicas * var.worker_cpu_request_millicores +
    local.executor_rollout_replicas * var.executor_cpu_request_millicores +
    local.agent_executor_rollout_replicas * var.agent_executor_cpu_request_millicores +
    local.ui_rollout_replicas * var.ui_cpu_request_millicores
  )

  tracecat_rollout_peak_memory_mib = (
    local.api_rollout_replicas * var.api_memory_request_mib +
    local.worker_rollout_replicas * var.worker_memory_request_mib +
    local.executor_rollout_replicas * var.executor_memory_request_mib +
    local.agent_executor_rollout_replicas * var.agent_executor_memory_request_mib +
    local.ui_rollout_replicas * var.ui_memory_request_mib
  )

  tracecat_rollout_peak_pods = (
    local.api_rollout_replicas +
    local.worker_rollout_replicas +
    local.executor_rollout_replicas +
    local.agent_executor_rollout_replicas +
    local.ui_rollout_replicas
  )

  capacity_headroom_multiplier = 1 + (var.capacity_headroom_percent / 100)

  # Use the smallest configured instance shape per node group for conservative capacity.
  on_demand_node_cpu_millicores = min([
    for instance_type in var.node_instance_types :
    data.aws_ec2_instance_type.node_group[instance_type].default_vcpus * 1000
  ]...)
  on_demand_node_memory_mib = min([
    for instance_type in var.node_instance_types :
    data.aws_ec2_instance_type.node_group[instance_type].memory_size
  ]...)

  guardrail_node_basis = (
    var.cluster_autoscaler_enabled || var.temporal_mode == "cloud"
    ? "node_min_size"
    : "node_desired_size"
  )
  guardrail_on_demand_node_count = (
    var.cluster_autoscaler_enabled || var.temporal_mode == "cloud"
    ? var.node_min_size
    : var.node_desired_size
  )
  guardrail_temporal_cpu_millicores = (
    var.temporal_mode == "self-hosted"
    ? var.temporal_guardrail_cpu_millicores
    : 0
  )
  guardrail_temporal_memory_mib = (
    var.temporal_mode == "self-hosted"
    ? var.temporal_guardrail_memory_mib
    : 0
  )
  guardrail_temporal_pod_count = (
    var.temporal_mode == "self-hosted"
    ? var.temporal_guardrail_pod_count
    : 0
  )

  guardrail_cpu_capacity_millicores = (
    local.guardrail_on_demand_node_count * local.on_demand_node_cpu_millicores
  )
  guardrail_memory_capacity_mib = (
    local.guardrail_on_demand_node_count * local.on_demand_node_memory_mib
  )
  guardrail_pod_eni_capacity = (
    local.guardrail_on_demand_node_count * var.pod_eni_capacity_per_node
  )

  tracecat_required_cpu_with_headroom_millicores = ceil(local.tracecat_rollout_peak_cpu_millicores * local.capacity_headroom_multiplier)
  tracecat_required_memory_with_headroom_mib     = ceil(local.tracecat_rollout_peak_memory_mib * local.capacity_headroom_multiplier)
  tracecat_required_pod_eni_with_reserve         = local.tracecat_rollout_peak_pods + var.pod_eni_capacity_reserved

  required_cpu_with_headroom_millicores = (
    local.tracecat_required_cpu_with_headroom_millicores +
    local.guardrail_temporal_cpu_millicores
  )
  required_memory_with_headroom_mib = (
    local.tracecat_required_memory_with_headroom_mib +
    local.guardrail_temporal_memory_mib
  )
  required_pod_eni_with_reserve = (
    local.tracecat_required_pod_eni_with_reserve +
    local.guardrail_temporal_pod_count
  )
}

check "tracecat_rollout_cpu_capacity" {
  assert {
    condition = local.required_cpu_with_headroom_millicores <= local.guardrail_cpu_capacity_millicores
    error_message = format(
      "Insufficient rollout CPU capacity for temporal_mode=%s: required %dm (%dm Tracecat workload with %d%% headroom%s), available %dm (%d on-demand nodes x %dm, using %s). Increase on-demand capacity or lower requests/reservations.",
      var.temporal_mode,
      local.required_cpu_with_headroom_millicores,
      local.tracecat_rollout_peak_cpu_millicores,
      var.capacity_headroom_percent,
      local.guardrail_temporal_cpu_millicores > 0 ? format(" + %dm Temporal reservation", local.guardrail_temporal_cpu_millicores) : "",
      local.guardrail_cpu_capacity_millicores,
      local.guardrail_on_demand_node_count,
      local.on_demand_node_cpu_millicores,
      local.guardrail_node_basis
    )
  }
}

check "tracecat_rollout_memory_capacity" {
  assert {
    condition = local.required_memory_with_headroom_mib <= local.guardrail_memory_capacity_mib
    error_message = format(
      "Insufficient rollout memory capacity for temporal_mode=%s: required %dMi (%dMi Tracecat workload with %d%% headroom%s), available %dMi (%d on-demand nodes x %dMi, using %s). Increase on-demand capacity or lower requests/reservations.",
      var.temporal_mode,
      local.required_memory_with_headroom_mib,
      local.tracecat_rollout_peak_memory_mib,
      var.capacity_headroom_percent,
      local.guardrail_temporal_memory_mib > 0 ? format(" + %dMi Temporal reservation", local.guardrail_temporal_memory_mib) : "",
      local.guardrail_memory_capacity_mib,
      local.guardrail_on_demand_node_count,
      local.on_demand_node_memory_mib,
      local.guardrail_node_basis
    )
  }
}

check "tracecat_rollout_pod_eni_capacity" {
  assert {
    condition = local.required_pod_eni_with_reserve <= local.guardrail_pod_eni_capacity
    error_message = format(
      "Insufficient pod-eni budget for temporal_mode=%s: required %d pods (%d Tracecat workload + %d reserve%s), available %d (%d on-demand nodes x %d, using %s). Increase on-demand capacity or pod_eni_capacity_per_node.",
      var.temporal_mode,
      local.required_pod_eni_with_reserve,
      local.tracecat_rollout_peak_pods,
      var.pod_eni_capacity_reserved,
      local.guardrail_temporal_pod_count > 0 ? format(" + %d Temporal reservation", local.guardrail_temporal_pod_count) : "",
      local.guardrail_pod_eni_capacity,
      local.guardrail_on_demand_node_count,
      var.pod_eni_capacity_per_node,
      local.guardrail_node_basis
    )
  }
}

check "node_group_architecture_consistency" {
  assert {
    condition = (
      length(local.on_demand_arch_mismatches) == 0 &&
      (!var.spot_node_group_enabled || length(local.spot_arch_mismatches) == 0)
    )
    error_message = format(
      "All node groups must use instance types matching node_architecture=%s. Incompatible on-demand types: %s. Incompatible spot types: %s.",
      var.node_architecture,
      length(local.on_demand_arch_mismatches) > 0 ? join(", ", local.on_demand_arch_mismatches) : "(none)",
      var.spot_node_group_enabled && length(local.spot_arch_mismatches) > 0 ? join(", ", local.spot_arch_mismatches) : "(none)"
    )
  }
}

# Generate random suffix for RDS identifier to avoid clashes during snapshot restore
resource "random_id" "rds_suffix" {
  byte_length = 4
}

# Generate random suffix for S3 bucket names to ensure global uniqueness
resource "random_id" "s3_suffix" {
  byte_length = 4
}
