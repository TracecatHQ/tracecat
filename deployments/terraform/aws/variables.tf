# AWS Configuration
variable "aws_region" {
  description = "AWS region for the infrastructure"
  type        = string
  default     = "us-west-2"
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
  default     = ["t3.large"]
}

variable "node_desired_size" {
  description = "Desired number of nodes in the node group"
  type        = number
  default     = 3
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

# Tracecat Configuration
variable "tracecat_version" {
  description = "Version of the Tracecat Helm chart to deploy"
  type        = string
  default     = ""
}

variable "tracecat_image_tag" {
  description = "Docker image tag for Tracecat services"
  type        = string
  default     = "latest"
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

variable "elasticache_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t3.micro"
}

# Temporal Configuration
variable "temporal_mode" {
  description = "Temporal deployment mode: 'self-hosted' or 'cloud'"
  type        = string
  default     = "self-hosted"

  validation {
    condition     = contains(["self-hosted", "cloud"], var.temporal_mode)
    error_message = "temporal_mode must be either 'self-hosted' or 'cloud'"
  }
}

variable "temporal_cloud_url" {
  description = "Temporal Cloud cluster URL (host:port) - required when temporal_mode is 'cloud'"
  type        = string
  default     = ""
}

variable "temporal_cloud_namespace" {
  description = "Temporal Cloud namespace - required when temporal_mode is 'cloud'"
  type        = string
  default     = ""
}

variable "temporal_cloud_api_key_secret_arn" {
  description = "ARN of AWS Secrets Manager secret containing Temporal Cloud API key (plain text) - required when temporal_mode is 'cloud'"
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

variable "ui_replicas" {
  description = "Number of UI replicas"
  type        = number
  default     = 1
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
