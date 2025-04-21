## AWS provider variables

variable "aws_region" {
  type        = string
  description = "AWS region (secrets and hosted zone must be in the same region)"
}

variable "aws_account_id" {
  type        = string
  description = "(Optional) Account ID to deploy Tracecat into. Only required if deploying cross-account."
  default     = null
}

variable "aws_role_name" {
  type        = string
  description = "(Optional) AWS role name for Terraform to assume to deploy Tracecat. Only required if deploying cross-account."
  default     = null
}

### DNS

variable "domain_name" {
  type        = string
  description = "The domain name to use for Tracecat"
}

variable "hosted_zone_id" {
  type        = string
  description = "The ID of the hosted zone in Route53"
}

### Security

variable "auth_types" {
  type    = string
  default = "google_oauth,saml"
}


variable "auth_allowed_domains" {
  type        = string
  description = "Comma separated list of allowed domains for authentication (e.g. `acme.com,acme.ai`)"
  default     = null
}


variable "setting_override_saml_enabled" {
  type        = string
  description = "Override the SAML setting"
  default     = null
}

variable "setting_override_oauth_google_enabled" {
  type        = string
  description = "Override the Google OAuth setting"
  default     = null
}

variable "setting_override_basic_auth_enabled" {
  type        = string
  description = "Override the basic auth setting"
  default     = null
}


### Images and Versions

variable "tracecat_image" {
  type    = string
  default = "ghcr.io/tracecathq/tracecat"
}

variable "tracecat_ui_image" {
  type    = string
  default = "ghcr.io/tracecathq/tracecat-ui"
}

variable "tracecat_image_tag" {
  type    = string
  default = "0.33.0"
}

variable "temporal_server_image" {
  type    = string
  default = "temporalio/auto-setup"
}

variable "temporal_server_image_tag" {
  type    = string
  default = "1.24.2"
}

variable "temporal_ui_image" {
  type    = string
  default = "temporalio/ui"
}

variable "temporal_ui_image_tag" {
  type    = string
  default = "2.32.0"
}

variable "force_new_deployment" {
  type        = bool
  description = "Force a new deployment of Tracecat services. Used to update services with new images."
  default     = false
}

variable "use_git_commit_sha" {
  type        = bool
  description = "Use the git commit SHA as the image tag"
  default     = false
}

variable "TFC_CONFIGURATION_VERSION_GIT_COMMIT_SHA" {
  description = "Terraform Cloud only: the git commit SHA of that triggered the run"
  type        = string
  default     = null
}

### Temporal configuration

variable "disable_temporal_ui" {
  type        = bool
  description = "Whether to disable the Temporal UI service in the deployment"
  default     = true
}

variable "disable_temporal_autosetup" {
  type        = bool
  description = "Whether to disable the Temporal auto-setup service in the deployment"
  default     = false
}

variable "temporal_cluster_url" {
  type        = string
  description = "Host and port of the Temporal server to connect to"
  default     = "temporal-service:7233"
}

variable "temporal_cluster_queue" {
  type        = string
  description = "Temporal task queue to use for client calls"
  default     = "default"
}

variable "temporal_namespace" {
  type        = string
  description = "Temporal namespace to use for client calls"
  default     = "default"
}


### Container Env Vars
# NOTE: sensitive variables are stored in secrets manager
# and specified directly in the task definition via a secret reference

variable "tracecat_app_env" {
  type        = string
  description = "The environment of the Tracecat application"
  default     = "production"
}

variable "log_level" {
  type        = string
  description = "Log level for the application"
  default     = "INFO"
}

variable "temporal_log_level" {
  type    = string
  default = "warn"
}

### Secret ARNs

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

variable "oauth_client_id_arn" {
  type        = string
  description = "The ARN of the secret containing the OAuth client ID (optional)"
  default     = null
}

variable "oauth_client_secret_arn" {
  type        = string
  description = "The ARN of the secret containing the OAuth client secret (optional)"
  default     = null
}

variable "saml_idp_metadata_url_arn" {
  type        = string
  description = "The ARN of the secret containing the SAML IDP metadata URL (optional)"
  default     = null
}

# Temporal UI

variable "temporal_auth_provider_url" {
  type        = string
  description = "The URL of the Temporal auth provider"
  default     = null
}

variable "temporal_auth_client_id_arn" {
  type        = string
  description = "The ARN of the secret containing the Temporal auth client ID (optional)"
  default     = null
}

variable "temporal_auth_client_secret_arn" {
  type        = string
  description = "The ARN of the secret containing the Temporal auth client secret (optional)"
  default     = null
}

# Temporal client

variable "temporal_api_key_arn" {
  type        = string
  description = "The ARN of the secret containing the Temporal API key (optional)"
  default     = null
}

### (Optional) Custom Integrations

variable "remote_repository_package_name" {
  type        = string
  description = "The package name of the remote repository"
  default     = null
}

variable "remote_repository_url" {
  type        = string
  description = "The URL of the remote repository"
  default     = null
}

### Compute / Memory

variable "api_cpu" {
  type    = string
  default = "1024"
}

variable "api_memory" {
  type    = string
  default = "2048"
}

variable "worker_cpu" {
  type    = string
  default = "4096"
}

variable "worker_memory" {
  type    = string
  default = "8192"
}

variable "executor_cpu" {
  type    = string
  default = "4096"
}

variable "executor_memory" {
  type    = string
  default = "8192"
}

variable "executor_client_timeout" {
  type    = string
  default = "120"
}

variable "ui_cpu" {
  type    = string
  default = "512"
}

variable "ui_memory" {
  type    = string
  default = "1024"
}

variable "temporal_cpu" {
  type    = string
  default = "2048"
}

variable "temporal_memory" {
  type    = string
  default = "4096"
}

variable "temporal_client_rpc_timeout" {
  type        = string
  description = "RPC timeout for Temporal client in seconds"
  default     = null
}

variable "temporal_num_history_shards" {
  type        = string
  description = "Number of history shards for Temporal"
  default     = "512"
}

variable "caddy_cpu" {
  type    = string
  default = "256"
}

variable "caddy_memory" {
  type    = string
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

### RDS Settings

variable "restore_from_snapshot" {
  type        = bool
  description = "Restore RDS instances from a snapshot"
  default     = false
}

variable "rds_backup_retention_period" {
  type        = number
  description = "The number of days to retain backups for RDS instances"
  default     = 7
}


### Prometheus Metrics

variable "metrics_auth_username" {
  description = "Username for basic auth on metrics endpoints"
  type        = string
  default     = "metrics"
}

variable "metrics_auth_password_hash" {
  description = "Bcrypt hash of the password for basic auth on metrics endpoints (required when enable_metrics_auth is true)"
  type        = string
  default     = null
  sensitive   = true
}

variable "enable_metrics" {
  description = "Whether to expose metrics endpoints with basic auth protection"
  type        = bool
  default     = false
}

variable "sentry_dsn" {
  description = "The Sentry DSN to use for error reporting"
  type        = string
  default     = null
  sensitive   = true
}
