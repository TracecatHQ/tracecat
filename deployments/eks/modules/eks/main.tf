# Fetch current AWS region and account
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
data "aws_vpc" "selected" {
  id = var.vpc_id
}

# Fetch AWS RDS CA certificate bundle for TLS verification
# https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html
data "http" "rds_ca_bundle" {
  url = "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"
}

locals {
  aws_region     = data.aws_region.current.region
  aws_account_id = data.aws_caller_identity.current.account_id

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
  s3_suffix             = random_id.s3_suffix.hex
  s3_attachments_bucket = "tracecat-attachments-${local.s3_suffix}"
  s3_registry_bucket    = "tracecat-registry-${local.s3_suffix}"
  s3_workflow_bucket    = "tracecat-workflow-${local.s3_suffix}"

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

  desired_node_count = var.node_desired_size + (var.spot_node_group_enabled ? var.spot_node_desired_size : 0)

  desired_cpu_capacity_millicores = local.desired_node_count * var.node_schedulable_cpu_millicores_per_node
  desired_memory_capacity_mib     = local.desired_node_count * var.node_schedulable_memory_mib_per_node
  desired_pod_eni_capacity        = local.desired_node_count * var.pod_eni_capacity_per_node

  required_cpu_with_reserve_millicores = local.tracecat_rollout_peak_cpu_millicores + var.capacity_reserved_cpu_millicores
  required_memory_with_reserve_mib     = local.tracecat_rollout_peak_memory_mib + var.capacity_reserved_memory_mib
  required_pod_eni_with_reserve        = local.tracecat_rollout_peak_pods + var.capacity_reserved_pod_eni
}

check "tracecat_rollout_cpu_capacity" {
  assert {
    condition = local.required_cpu_with_reserve_millicores <= local.desired_cpu_capacity_millicores
    error_message = format(
      "Insufficient rollout CPU capacity: required %dm (%dm workload + %dm reserve), available %dm (%d nodes x %dm). Increase node_desired_size/spot_node_desired_size or lower CPU requests.",
      local.required_cpu_with_reserve_millicores,
      local.tracecat_rollout_peak_cpu_millicores,
      var.capacity_reserved_cpu_millicores,
      local.desired_cpu_capacity_millicores,
      local.desired_node_count,
      var.node_schedulable_cpu_millicores_per_node
    )
  }
}

check "tracecat_rollout_memory_capacity" {
  assert {
    condition = local.required_memory_with_reserve_mib <= local.desired_memory_capacity_mib
    error_message = format(
      "Insufficient rollout memory capacity: required %dMi (%dMi workload + %dMi reserve), available %dMi (%d nodes x %dMi). Increase node_desired_size/spot_node_desired_size or lower memory requests.",
      local.required_memory_with_reserve_mib,
      local.tracecat_rollout_peak_memory_mib,
      var.capacity_reserved_memory_mib,
      local.desired_memory_capacity_mib,
      local.desired_node_count,
      var.node_schedulable_memory_mib_per_node
    )
  }
}

check "tracecat_rollout_pod_eni_capacity" {
  assert {
    condition = local.required_pod_eni_with_reserve <= local.desired_pod_eni_capacity
    error_message = format(
      "Insufficient pod-eni budget: required %d pods (%d workload + %d reserve), available %d (%d nodes x %d). Increase node_desired_size/spot_node_desired_size or pod_eni_capacity_per_node.",
      local.required_pod_eni_with_reserve,
      local.tracecat_rollout_peak_pods,
      var.capacity_reserved_pod_eni,
      local.desired_pod_eni_capacity,
      local.desired_node_count,
      var.pod_eni_capacity_per_node
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
