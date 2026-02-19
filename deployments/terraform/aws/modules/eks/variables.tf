# Cluster Configuration
variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
  default     = "tracecat-eks"
}

variable "cluster_version" {
  description = "Kubernetes version for the EKS cluster"
  type        = string
  default     = "1.34"
}

# Network Configuration
variable "vpc_id" {
  description = "ID of the VPC"
  type        = string
}

variable "private_subnet_ids" {
  description = "IDs of the private subnets for EKS"
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "IDs of the public subnets for ALB"
  type        = list(string)
}

variable "acm_certificate_arn" {
  description = "ARN of the ACM certificate for ALB HTTPS"
  type        = string
}

# Node Group Configuration
variable "node_instance_types" {
  description = "Instance types for the EKS node group"
  type        = list(string)
  default     = ["m7g.2xlarge"]
}

variable "node_ami_type" {
  description = "AMI type for the EKS node group (AL2023_ARM_64_STANDARD or AL2023_x86_64_STANDARD)"
  type        = string
  default     = "AL2023_ARM_64_STANDARD"
}

variable "node_desired_size" {
  description = "Desired number of nodes in the node group"
  type        = number
  default     = 8
}

variable "node_min_size" {
  description = "Minimum number of nodes in the node group"
  type        = number
  default     = 8
}

variable "node_max_size" {
  description = "Maximum number of nodes in the node group"
  type        = number
  default     = 12
}

variable "node_disk_size" {
  description = "Disk size in GB for worker nodes"
  type        = number
  default     = 50
}

variable "spot_node_group_enabled" {
  description = "Enable the spot managed node group."
  type        = bool
  default     = true
}

variable "spot_node_instance_types" {
  description = "Instance types for the spot managed node group."
  type        = list(string)
  default     = ["m7g.2xlarge"]
}

variable "spot_node_desired_size" {
  description = "Desired number of nodes in the spot managed node group."
  type        = number
  default     = 2
}

variable "spot_node_min_size" {
  description = "Minimum number of nodes in the spot managed node group."
  type        = number
  default     = 2
}

variable "spot_node_max_size" {
  description = "Maximum number of nodes in the spot managed node group."
  type        = number
  default     = 4
}

# Tracecat Configuration
variable "domain_name" {
  description = "Domain name for Tracecat"
  type        = string
}

variable "hosted_zone_id" {
  description = "Route53 hosted zone ID for DNS record creation"
  type        = string
}

variable "tracecat_image_tag" {
  description = "Docker image tag for Tracecat services"
  type        = string
  default     = "1.0.0-beta.13"
}

variable "tracecat_ingress_split" {
  description = "Split Tracecat ingress into separate UI and API ingresses."
  type        = bool
  default     = true
}

variable "superadmin_email" {
  description = "Email address for the Tracecat superadmin"
  type        = string
}

variable "tracecat_secrets_arn" {
  description = "ARN of AWS Secrets Manager secret containing Tracecat secrets (JSON with keys: dbEncryptionKey, serviceKey, signingSecret, userAuthSecret)"
  type        = string
}

# Data Services (always provisioned)

variable "rds_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.2xlarge"
}

variable "rds_engine_version" {
  description = "Exact Postgres engine version for the RDS instance (for example, 16.12)"
  type        = string
  default     = "16.12"
}

variable "rds_allocated_storage" {
  description = "Allocated storage for RDS in GB"
  type        = number
  default     = 50
}

variable "rds_storage_type" {
  description = "RDS storage type"
  type        = string
  default     = "gp3"

  validation {
    condition     = contains(["gp2", "gp3", "io1", "io2"], var.rds_storage_type)
    error_message = "rds_storage_type must be one of: gp2, gp3, io1, io2."
  }
}

variable "rds_apply_immediately" {
  description = "Whether to apply RDS modifications immediately instead of during the next maintenance window"
  type        = bool
  default     = false
}

variable "rds_master_username" {
  description = "Master username for RDS"
  type        = string
  default     = "tracecat"
}

variable "rds_snapshot_identifier" {
  description = "Optional RDS snapshot identifier or ARN to restore from"
  type        = string
  default     = ""
}

variable "rds_database_insights_mode" {
  description = "RDS Database Insights mode. Use 'advanced' to enable Advanced Database Insights (CloudWatch Database Insights)."
  type        = string
  default     = "advanced"

  validation {
    condition     = contains(["standard", "advanced"], var.rds_database_insights_mode)
    error_message = "rds_database_insights_mode must be 'standard' or 'advanced'."
  }
}

variable "rds_skip_final_snapshot" {
  description = "Whether to skip the final RDS snapshot on deletion"
  type        = bool
  default     = false
}

variable "rds_deletion_protection" {
  description = "Whether to enable deletion protection for the RDS instance"
  type        = bool
  default     = true
}

variable "rds_password_rotation_schedule" {
  description = "Rotation schedule expression for the RDS master password (e.g. 'rate(365 days)', 'rate(7 days)')"
  type        = string
  default     = "rate(365 days)"
}

variable "elasticache_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t4g.medium"
}

# Temporal Configuration
variable "temporal_mode" {
  description = "Temporal deployment mode: 'self-hosted' or external ('cloud')"
  type        = string
  default     = "self-hosted"

  validation {
    condition     = contains(["self-hosted", "cloud"], var.temporal_mode)
    error_message = "temporal_mode must be either 'self-hosted' or 'cloud'"
  }
}

variable "temporal_cluster_url" {
  description = "Temporal cluster URL (host:port) - required when temporal_mode is 'cloud'"
  type        = string
  default     = ""
}

variable "temporal_cluster_namespace" {
  description = "Temporal cluster namespace - required when temporal_mode is 'cloud'"
  type        = string
  default     = ""
}

variable "temporal_secret_arn" {
  description = "ARN of AWS Secrets Manager secret containing Temporal API key (plain text) - required when temporal_mode is 'cloud'"
  type        = string
  default     = ""
}

variable "external_secrets_namespace" {
  description = "Kubernetes namespace for the External Secrets Operator."
  type        = string
  default     = "external-secrets"
}

variable "external_secrets_service_account_name" {
  description = "Service account name for the External Secrets Operator."
  type        = string
  default     = "external-secrets"
}

variable "external_dns_namespace" {
  description = "Kubernetes namespace for ExternalDNS."
  type        = string
  default     = "external-dns"
}

variable "external_dns_service_account_name" {
  description = "Service account name for ExternalDNS."
  type        = string
  default     = "external-dns"
}

# Replica Counts
variable "api_replicas" {
  description = "Number of API replicas"
  type        = number
  default     = 2
}

variable "worker_replicas" {
  description = "Number of worker replicas"
  type        = number
  default     = 4
}

variable "executor_replicas" {
  description = "Number of executor replicas"
  type        = number
  default     = 4
}

variable "executor_queue" {
  description = "Queue name for executor workers"
  type        = string
  default     = "shared-action-queue"
}

variable "executor_backend" {
  description = "Executor backend: 'pool', 'ephemeral', 'direct', or 'auto'"
  type        = string
  default     = "ephemeral"
}

variable "agent_executor_replicas" {
  description = "Number of agent-executor replicas"
  type        = number
  default     = 2
}

variable "agent_executor_queue" {
  description = "Queue name for agent-executor workers"
  type        = string
  default     = "shared-agent-queue"
}

variable "agent_executor_backend" {
  description = "Agent executor backend: 'pool', 'ephemeral', 'direct', or 'auto'"
  type        = string
  default     = "ephemeral"
}

variable "ui_replicas" {
  description = "Number of UI replicas"
  type        = number
  default     = 2
}

# Tracecat resource requests (also applied as limits)
variable "api_cpu_request_millicores" {
  description = "API CPU request in millicores"
  type        = number
  default     = 2000
}

variable "api_memory_request_mib" {
  description = "API memory request in MiB"
  type        = number
  default     = 4096
}

variable "worker_cpu_request_millicores" {
  description = "Worker CPU request in millicores"
  type        = number
  default     = 2000
}

variable "worker_memory_request_mib" {
  description = "Worker memory request in MiB"
  type        = number
  default     = 2048
}

variable "executor_cpu_request_millicores" {
  description = "Executor CPU request in millicores"
  type        = number
  default     = 4000
}

variable "executor_memory_request_mib" {
  description = "Executor memory request in MiB"
  type        = number
  default     = 8192
}

variable "agent_executor_cpu_request_millicores" {
  description = "Agent executor CPU request in millicores"
  type        = number
  default     = 2000
}

variable "agent_executor_memory_request_mib" {
  description = "Agent executor memory request in MiB"
  type        = number
  default     = 8192
}

variable "ui_cpu_request_millicores" {
  description = "UI CPU request in millicores"
  type        = number
  default     = 500
}

variable "ui_memory_request_mib" {
  description = "UI memory request in MiB"
  type        = number
  default     = 512
}

# Plan-time rollout guardrails (capacity model inputs)
variable "node_schedulable_cpu_millicores_per_node" {
  description = "Schedulable CPU per worker node in millicores used for rollout capacity guardrails"
  type        = number
  default     = 8000
}

variable "node_schedulable_memory_mib_per_node" {
  description = "Schedulable memory per worker node in MiB used for rollout capacity guardrails"
  type        = number
  default     = 32768
}

variable "pod_eni_capacity_per_node" {
  description = "Estimated pod-eni budget per node used for rollout guardrails"
  type        = number
  default     = 16
}

variable "rollout_surge_percent" {
  description = "Deployment rollout surge percentage used for capacity guardrails"
  type        = number
  default     = 25
}

variable "capacity_reserved_cpu_millicores" {
  description = "Reserved CPU headroom in millicores for system and auxiliary workloads in guardrails"
  type        = number
  default     = 3000
}

variable "capacity_reserved_memory_mib" {
  description = "Reserved memory headroom in MiB for system and auxiliary workloads in guardrails"
  type        = number
  default     = 8192
}

variable "capacity_reserved_pod_eni" {
  description = "Reserved pod-eni headroom for system and auxiliary workloads in guardrails"
  type        = number
  default     = 8
}

# WAF Configuration
variable "enable_waf" {
  description = "Enable AWS WAFv2 Web ACL for ALB protection"
  type        = bool
  default     = true
}

variable "waf_rate_limit" {
  description = "Maximum number of requests per 5-minute period per IP before rate limiting (WAF rate-based rule)"
  type        = number
  default     = 2000
}

# Observability Configuration
variable "enable_observability" {
  description = "Enable Grafana Cloud observability (K8s Monitoring, CloudWatch Metric Streams)"
  type        = bool
  default     = false
}

variable "grafana_cloud_prometheus_url" {
  description = "Grafana Cloud Prometheus remote write URL (e.g., https://prometheus-prod-01-us-east-0.grafana.net/api/prom/push)"
  type        = string
  default     = ""
}

variable "grafana_cloud_prometheus_username" {
  description = "Grafana Cloud Prometheus username (numeric instance ID)"
  type        = string
  default     = ""
}

variable "grafana_cloud_loki_url" {
  description = "Grafana Cloud Loki push URL (e.g., https://logs-prod-us-east-0.grafana.net/loki/api/v1/push)"
  type        = string
  default     = ""
}

variable "grafana_cloud_loki_username" {
  description = "Grafana Cloud Loki username (numeric instance ID)"
  type        = string
  default     = ""
}

variable "grafana_cloud_credentials_secret_arn" {
  description = "ARN of Secrets Manager secret with Grafana Cloud credentials (JSON: {\"metrics_write_token\": \"...\"}). Synced to cluster via ESO."
  type        = string
  default     = ""
}

variable "grafana_cloud_firehose_endpoint" {
  description = "Grafana Cloud Firehose endpoint URL for CloudWatch Metric Streams"
  type        = string
  default     = ""
}

variable "observability_log_retention_days" {
  description = "Retention in days for Firehose delivery log groups and S3 failed-delivery backups"
  type        = number
  default     = 30
}

# Tags
variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}

# Feature Flags
variable "feature_flags" {
  description = "Comma-separated enterprise feature flags (maps to enterprise.featureFlags)"
  type        = string
  default     = ""
}

# Auth Configuration
variable "auth_types" {
  description = "Comma-separated authentication types (e.g., 'oidc', 'basic,saml', 'basic,oidc')"
  type        = string
  default     = "oidc"
}

# OIDC Configuration
variable "oidc_issuer" {
  description = "OIDC issuer URL (e.g., https://accounts.google.com)"
  type        = string
  default     = ""
}

variable "oidc_client_id" {
  description = "OIDC client ID"
  type        = string
  default     = ""
}

variable "oidc_client_secret" {
  description = "OIDC client secret"
  type        = string
  default     = ""
  sensitive   = true
}

variable "oidc_scopes" {
  description = "OIDC scopes to request (space-separated, e.g., 'openid email profile')"
  type        = string
  default     = ""
}

variable "ee_multi_tenant" {
  description = "Enable enterprise multi-tenant mode"
  type        = bool
  default     = false
}
