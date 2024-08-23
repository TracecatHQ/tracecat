## Top-level variables

variable "aws_region" {
  default = "us-east-2"
}

variable "az_count" {
    description = "Number of AZs to cover in a given region"
    default = "2"
}

variable "log_level" {
  type    = string
  default = "INFO"
}

variable "tracecat_app_env" {
  type = string
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
  type = string
  description = "The ARN of the secret containing the Tracecat database password"
}

variable "temporal_db_password_arn" {
  type = string
  description = "The ARN of the secret containing the Temporal database password"
}

variable "tracecat_db_encryption_key_arn" {
  type = string
  description = "The ARN of the secret containing the Tracecat database encryption key"
}

variable "tracecat_service_key_arn" {
  type = string
  description = "The ARN of the secret containing the Tracecat service key"
}

variable "tracecat_signing_secret_arn" {
  type = string
  description = "The ARN of the secret containing the Tracecat signing secret"
}

variable "oauth_client_id" {
  type = string
  description = "The OAuth client ID (optional)"
  default = null
}

variable "oauth_client_secret" {
  type = string
  description = "The OAuth client secret (optional)"
  default = null
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

variable "tracecat_api_url" {
  type = string
  description = "The URL of the Tracecat API"
}

variable "tracecat_api_root_path" {
  type = string
  description = "The root path of the Tracecat API"
  default = "/api"
}

variable "log_level" {
  type = string
  description = "Log level for the application"
}

variable "tracecat_app_env" {
  type = string
  description = "The environment of the Tracecat application"
}

variable "tracecat_db_sslmode" {
  type = string
  description = "SSL mode for the database connection"
}

variable "tracecat_db_uri" {
  type = string
  description = "Database URI"
}

variable "tracecat_public_runner_url" {
  type = string
  description = "Public URL for the runner"
}

variable "tracecat_public_app_url" {
  type = string
  description = "Public URL for the application"
}

variable "tracecat_allow_origins" {
  type = string
  description = "Allowed origins for CORS"
}

# UI

variable "auth_types" {
  type = string
  default = "basic,google_oauth"
}

# Temporal

variable "temporal_cluster_url" {
  type = string
  description = "Temporal cluster URL"
}

variable "temporal_cluster_queue" {
  type = string
  description = "Temporal cluster queue"
}

### Locals to organize env vars
locals {
  api_env = {
    LOG_LEVEL                    = var.log_level
    TRACECAT__API_URL            = var.tracecat_api_url
    TRACECAT__API_ROOT_PATH      = var.tracecat_api_root_path
    TRACECAT__APP_ENV            = var.tracecat_app_env
    TRACECAT__DB_SSLMODE         = var.tracecat_db_sslmode
    TRACECAT__DB_URI             = var.tracecat_db_uri
    TRACECAT__PUBLIC_RUNNER_URL  = var.tracecat_public_runner_url
    TRACECAT__PUBLIC_APP_URL     = var.tracecat_public_app_url
    TRACECAT__ALLOW_ORIGINS      = var.tracecat_allow_origins
    TRACECAT__AUTH_TYPES         = var.auth_types
    TEMPORAL__CLUSTER_URL        = var.temporal_cluster_url
    TEMPORAL__CLUSTER_QUEUE      = var.temporal_cluster_queue
  }
  worker_env = {
    LOG_LEVEL                    = var.log_level
    TRACECAT__API_URL            = var.tracecat_api_url
    TRACECAT__API_ROOT_PATH      = var.tracecat_api_root_path
    TRACECAT__APP_ENV            = var.tracecat_app_env
    TRACECAT__DB_SSLMODE         = var.tracecat_db_sslmode
    TRACECAT__DB_URI             = var.tracecat_db_uri
    TRACECAT__PUBLIC_RUNNER_URL  = var.tracecat_public_runner_url
    TEMPORAL__CLUSTER_URL        = var.temporal_cluster_url
    TEMPORAL__CLUSTER_QUEUE      = var.temporal_cluster_queue
  }
  ui_env = {
    NEXT_PUBLIC_API_URL          = "${var.tracecat_api_url}/api/"
    NEXT_PUBLIC_APP_ENV          = var.tracecat_app_env
    NEXT_PUBLIC_APP_URL          = var.tracecat_public_app_url
    NEXT_PUBLIC_AUTH_TYPES       = var.auth_types
    NEXT_SERVER_API_URL          = "http://api-service:8000"  # Service connect DNS name
    NODE_ENV                     = var.tracecat_app_env
  }
  temporal_env = {
    TEMPORAL__CLUSTER_URL        = var.temporal_cluster_url
    TEMPORAL__CLUSTER_QUEUE      = var.temporal_cluster_queue
  }
}
