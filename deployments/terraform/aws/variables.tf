# AWS Configuration
variable "aws_region" {
  description = "AWS region for the infrastructure"
  type        = string
  default     = "us-west-2"
}

variable "aws_role_name" {
  description = "(Optional) IAM role name to assume for cross-account deployment"
  type        = string
  default     = null
}

variable "aws_account_id" {
  description = "(Optional) AWS account ID to deploy into. Required when aws_role_name is set."
  type        = string
  default     = null

  validation {
    condition     = var.aws_role_name == null || var.aws_account_id != null
    error_message = "aws_account_id must be set when aws_role_name is provided."
  }
}

# Domain and DNS
variable "domain_name" {
  description = "Domain name for Tracecat (e.g., tracecat.example.com)"
  type        = string
}

variable "hosted_zone_id" {
  description = "Route53 hosted zone ID for DNS validation"
  type        = string
}

# Network Configuration
variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones to use"
  type        = number
  default     = 2
}

# EKS Configuration
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
  default     = 2
}

variable "node_min_size" {
  description = "Minimum number of nodes in the node group"
  type        = number
  default     = 2
}

variable "node_max_size" {
  description = "Maximum number of nodes in the node group"
  type        = number
  default     = 5
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
  default     = 5
}

# Tracecat Configuration
variable "tracecat_version" {
  description = "Version of the Tracecat Helm chart to deploy"
  type        = string
  default     = ""
}

variable "tracecat_image_tag" {
  description = "Docker image tag for Tracecat services"
  type        = string
  default     = "1.0.0-beta.0"
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
  default     = "db.t4g.medium"
}

variable "rds_allocated_storage" {
  description = "Allocated storage for RDS in GB"
  type        = number
  default     = 20
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

variable "rds_allow_vpc_cidr_fallback" {
  description = "Allow PostgreSQL access from the VPC CIDR. Required for t-series instances which don't support SecurityGroupPolicy (trunk ENI). Only disable if using m5/c5/r5/m6g/c6g/r6g instance types that support Security Groups for Pods."
  type        = bool
  default     = true
}

variable "elasticache_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t3.micro"
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
  default     = 2
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
  default     = 1
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
  default     = 1
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

# Tags
variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default = {
    Project   = "tracecat"
    ManagedBy = "terraform"
  }
}

# Feature Flags
variable "feature_flags" {
  description = "Comma-separated feature flags (e.g. 'git-sync,case-tasks')"
  type        = string
  default     = ""
}
