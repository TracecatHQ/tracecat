## Top-level variables

variable "aws_region" {
  default = "us-east-2"
}

variable "az_count" {
  description = "Number of AZs to cover in a given region"
  default     = "2"
}

variable "log_level" {
  type    = string
  default = "INFO"
}

variable "tracecat_app_env" {
  type    = string
  default = "production"
}

### Images and Versions

variable "temporal_server_image" {
  default = "temporalio/auto-setup"
}

variable "temporal_server_image_tag" {
  #default = "latest"
  default = "1.24.2"
}

variable "tracecat_image_api" {
  default = "ghcr.io/tracecathq/tracecat"
}

variable "tracecat_image_api_tag" {
  #default = "latest"
  default = "0.5.2"
}

### Secret ARNs

variable "tracecat_db_password_arn" {
  type        = string
  description = "The ARN of the secret containing the Tracecat database password"
}

variable "temporal_db_password_arn" {
  type        = string
  description = "The ARN of the secret containing the Temporal database password"
}

variable "tracecat_db_encryption_key_arn" {
  type        = string
  description = "The ARN of the secret containing the Tracecat database encryption key"
}

variable "tracecat_service_key_arn" {
  type        = string
  description = "The ARN of the secret containing the Tracecat service key"
}

variable "tracecat_signing_secret_arn" {
  type        = string
  description = "The ARN of the secret containing the Tracecat signing secret"
}

variable "oauth_client_id" {
  type        = string
  description = "The OAuth client ID (optional)"
  default     = null
}

variable "oauth_client_secret" {
  type        = string
  description = "The OAuth client secret (optional)"
  default     = null
}

### DNS

variable "hosted_zone_id" {
  description = "The ID of the hosted zone in Route53"
}

variable "domain_name" {
  description = "The domain name to use for the application"
}

### Compute / Memory

variable "api_cpu" {
  default = "256"
}

variable "api_memory" {
  default = "512"
}

variable "worker_cpu" {
  default = "256"
}

variable "worker_memory" {
  default = "512"
}

variable "ui_cpu" {
  default = "256"
}

variable "ui_memory" {
  default = "512"
}

variable "temporal_cpu" {
  default = "256"
}

variable "temporal_memory" {
  default = "512"
}

variable "db_instance_class" {
  type    = string
  default = "db.t3"
}

variable "db_instance_size" {
  type    = string
  default = "medium"
}

### Container Env Vars
# NOTE: sensitive variables are stored in secrets manager
# and specified directly in the task definition via a secret reference

### Container Env Vars
# PUBLIC_APP_URL = https://{var.domain_name}
# PUBLIC_API_URL= https://{var.domain_name}/api/
# INTERNAL_API_URL =  http://api-service:8000


variable "log_level" {
  type        = string
  description = "Log level for the application"
}

variable "tracecat_app_env" {
  type        = string
  description = "The environment of the Tracecat application"
}

variable "tracecat_db_sslmode" {
  type        = string
  description = "SSL mode for the database connection"
}

variable "tracecat_db_uri" {
  type        = string
  description = "Database URI"
}

variable "tracecat_public_runner_url" {
  type        = string
  description = "Public URL for the runner"
}

variable "tracecat_public_app_url" {
  type        = string
  description = "Public URL for the application"
}

variable "tracecat_allow_origins" {
  type        = string
  description = "Allowed origins for CORS"
}

# UI

variable "auth_types" {
  type    = string
  default = "basic,google_oauth"
}

### Locals to organize env vars
locals {

  public_app_url         = "https://${var.domain_name}"
  public_api_url         = "https://${var.domain_name}/api"
  internal_api_url       = "http://api-service:8000" # Service connect DNS name
  temporal_cluster_url   = "temporal-service:7233"
  temporal_cluster_queue = "tracecat-task-queue"

  # Tracecat postgres env vars
  # See: https://github.com/TracecatHQ/tracecat/blob/abd5ff/tracecat/db/engine.py#L21
  tracecat_db_configs = {
    # NOTE: still missing
    # TRACECAT__DB_ENDPOINT which is the hostname of the RDS instance (from RDS resource)
    # TRACECAT__DB_PASS which is the password for the database (from secrets manager)
    TRACECAT__DB_USER     = "postgres"
    TRACECAT__DB_PORT     = "5432"
    TRACECAT__DB_NAME     = "postgres"  # Hardcoded in RDS resource configs
  }

  api_env = merge({
    LOG_LEVEL                   = var.log_level
    TRACECAT__API_URL           = local.internal_api_url
    TRACECAT__API_ROOT_PATH     = "/api"
    TRACECAT__APP_ENV           = var.tracecat_app_env    
    TRACECAT__PUBLIC_RUNNER_URL = local.public_api_url
    TRACECAT__PUBLIC_APP_URL    = var.tracecat_public_app_url
    TRACECAT__ALLOW_ORIGINS     = var.tracecat_allow_origins
    TRACECAT__AUTH_TYPES        = var.auth_types
    TEMPORAL__CLUSTER_URL       = local.temporal_cluster_url
    TEMPORAL__CLUSTER_QUEUE     = local.temporal_cluster_queue
  }, local.tracecat_db_configs)

  worker_env = merge({
    LOG_LEVEL                   = var.log_level
    TRACECAT__API_URL           = local.internal_api_url
    TRACECAT__API_ROOT_PATH     = "/api"
    TRACECAT__APP_ENV           = var.tracecat_app_env
    TRACECAT__PUBLIC_RUNNER_URL = local.public_api_url
    TEMPORAL__CLUSTER_URL       = local.temporal_cluster_url
    TEMPORAL__CLUSTER_QUEUE     = local.temporal_cluster_queue
    }, local.tracecat_db_configs)

  ui_env = {
    NEXT_PUBLIC_API_URL    = local.public_api_url
    NEXT_PUBLIC_APP_ENV    = var.tracecat_app_env
    NEXT_PUBLIC_APP_URL    = var.tracecat_public_app_url
    NEXT_PUBLIC_AUTH_TYPES = var.auth_types
    NEXT_SERVER_API_URL    = local.internal_api_url
    NODE_ENV               = var.tracecat_app_env
  }

  temporal_env = {
    # NOTE: still missing
    # POSTGRES_SEEDS which is the hostname of the RDS instance (from RDS resource)
    # POSTGRES_PWD which is the password for the database (from secrets manager)
    DB                      = "postgres12"
    DB_PORT                 = "5432"
    POSTGRES_USER           = "postgres" # Hardcoded in RDS resource configs
    LOG_LEVEL               = "warn"
    # NOTE: The following two variables are required for
    # Temporal in Fargate. They are not required in docker compose.
    TEMPORAL_BROADCAST_ADDRESS = "0.0.0.0"
    BIND_ON_IP                 = "0.0.0.0"
  }
}
