## AWS provider variables

variable "aws_region" {
  type        = string
  description = "AWS region (secrets and hosted zone must be in the same region)"
}

variable "aws_role_arn" {
  type        = string
  description = "The ARN of the AWS role to assume"
  default     = null
}

### Networking

variable "is_internal" {
  type        = bool
  description = "Whether the ALB is internal or public"
  default     = false
}

variable "vpc_id" {
  type        = string
  description = "The ID of the VPC"
}

variable "public_subnet_ids" {
  type        = list(string)
  description = "The IDs of the public subnets"
}

variable "private_subnet_ids" {
  type        = list(string)
  description = "The IDs of the private subnets"
}

variable "private_route_table_ids" {
  type        = list(string)
  description = "The IDs of the private route tables"
}

variable "allowed_inbound_cidr_blocks" {
  description = "List of CIDR blocks allowed to access the ALB"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "enable_waf" {
  description = "Whether to enable WAF for the ALB"
  type        = bool
  default     = true
}


### DNS

variable "domain_name" {
  type        = string
  description = "The domain name to use for Tracecat"
}

variable "hosted_zone_id" {
  type        = string
  description = "The hosted zone ID associated with the Tracecat domain"
}

variable "acm_certificate_arn" {
  type        = string
  description = "The ARN of the ACM certificate to use for Tracecat"
}

### Security

variable "auth_types" {
  type    = string
  default = "saml"
}

variable "auth_allowed_domains" {
  type        = string
  description = "Comma separated list of allowed domains for authentication (e.g. `acme.com,acme.ai`)"
  default     = null
}

variable "auth_min_password_length" {
  type        = number
  description = "Minimum password length for local authentication"
  default     = 12
}

variable "auth_superadmin_email" {
  type        = string
  description = "Email address of the superadmin user for Tracecat authentication"
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
  default = "1.0.0-beta.14"
}

variable "temporal_server_image" {
  type    = string
  default = "temporalio/auto-setup"
}

variable "temporal_server_image_tag" {
  type    = string
  default = "1.29.1"
}

variable "temporal_ui_image" {
  type    = string
  default = "temporalio/ui"
}

variable "temporal_ui_image_tag" {
  type    = string
  default = "2.43.3"
}

variable "force_new_deployment" {
  type        = bool
  description = "Force a new deployment of Tracecat services. Used to update services with new images."
  default     = false
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

# NOTE: Modifying feature flags requires a Tracecat Enterprise license.
variable "feature_flags" {
  type        = string
  description = "Comma separated list of Tracecat feature flags to enable. Requires Tracecat Enterprise license to modify."
  default     = ""
}

variable "ee_multi_tenant" {
  type        = bool
  description = "Enable enterprise multi-tenant mode"
  default     = false
}

variable "result_externalization_enabled" {
  type        = bool
  description = "Enable externalization of large workflow payloads to blob storage"
  default     = true
}

variable "collection_manifests_enabled" {
  type        = bool
  description = "Enable collection manifest externalization"
  default     = true
}

variable "result_externalization_threshold_bytes" {
  type        = number
  description = "Threshold in bytes above which workflow payloads are externalized"
  default     = 128000
}

variable "workflow_artifact_retention_days" {
  type        = number
  description = "Retention period in days for workflow artifacts in blob storage (0 disables expiration)"
  default     = 30
}

### Database Connection Pool

variable "db_max_overflow" {
  type        = string
  description = "The maximum number of connections to allow in the DB pool"
  default     = "30"
}

variable "db_pool_size" {
  type        = string
  description = "The size of the database connection pool"
  default     = "30"
}

variable "db_pool_timeout" {
  type        = string
  description = "The timeout in seconds for acquiring a DB connection from the pool"
  default     = "30"
}

variable "db_pool_recycle" {
  type        = string
  description = "The time in seconds after which pool connections are recycled"
  default     = "1800"
}

variable "db_max_overflow_executor" {
  type        = string
  description = "The maximum number of connections to allow in the DB pool"
  default     = "30"
}

variable "db_pool_size_executor" {
  type        = string
  description = "The size of the database connection pool"
  default     = "30"
}

### Context Compression Configuration

variable "context_compression_enabled" {
  type        = bool
  description = "Enable compression of large action results in workflow contexts"
  default     = true
}

variable "context_compression_threshold_kb" {
  type        = number
  description = "Threshold in KB above which action results are compressed"
  default     = 16
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

variable "oidc_issuer" {
  type        = string
  description = "OIDC issuer URL (optional)"
  default     = null
}

variable "oidc_scopes" {
  type        = string
  description = "OIDC scopes string (space-delimited)"
  default     = "openid profile email"
}

variable "oidc_client_id_arn" {
  type        = string
  description = "The ARN of the secret containing the OIDC client ID (optional)"
  default     = null
}

variable "oidc_client_secret_arn" {
  type        = string
  description = "The ARN of the secret containing the OIDC client secret (optional)"
  default     = null
}

variable "user_auth_secret_arn" {
  type        = string
  description = "The ARN of the secret containing USER_AUTH_SECRET (optional)"
  default     = null
}

variable "saml_idp_metadata_url_arn" {
  type        = string
  description = "The ARN of the secret containing the SAML IDP metadata URL (optional)"
  default     = null
}

variable "saml_allow_unsolicited" {
  type        = bool
  description = "Allow unsolicited SAML responses"
  default     = false
}

variable "saml_authn_requests_signed" {
  type        = bool
  description = "Require signed SAML authn requests"
  default     = false
}

variable "saml_signed_assertions" {
  type        = bool
  description = "Require signed SAML assertions"
  default     = true
}

variable "saml_signed_responses" {
  type        = bool
  description = "Require signed SAML responses"
  default     = true
}

variable "saml_verify_ssl_entity" {
  type        = bool
  description = "Verify SSL certificates for SAML entity operations"
  default     = true
}

variable "saml_verify_ssl_metadata" {
  type        = bool
  description = "Verify SSL certificates for SAML metadata operations"
  default     = true
}

variable "saml_ca_certs_arn" {
  type        = string
  description = "The ARN of the secret containing SAML CA certs (optional)"
  default     = null
}

variable "saml_metadata_cert_arn" {
  type        = string
  description = "The ARN of the secret containing SAML metadata cert (optional)"
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

### Compute / Memory

variable "ui_cpu" {
  type    = string
  default = "1024"
}

variable "ui_memory" {
  type    = string
  default = "2048"
}

variable "api_cpu" {
  type    = string
  default = "1024"
}

variable "api_memory" {
  type    = string
  default = "2048"
}

variable "api_desired_count" {
  type        = number
  description = "Desired number of API instances to run"
  default     = 2
}

variable "worker_cpu" {
  type    = string
  default = "2048"
}

variable "worker_memory" {
  type    = string
  default = "4096"
}

variable "worker_desired_count" {
  type        = number
  description = "Desired number of worker instances to run"
  default     = 2
}

variable "executor_cpu" {
  type    = string
  default = "4096"
}

variable "executor_memory" {
  type    = string
  default = "8192"
}

variable "executor_desired_count" {
  type        = number
  description = "Desired number of executor instances to run"
  default     = 1
}

variable "executor_client_timeout" {
  type    = string
  default = "900"
}

variable "executor_queue" {
  type        = string
  description = "Task queue for executor workers"
  default     = "shared-action-queue"
}

variable "executor_worker_pool_size" {
  type        = string
  description = "Executor worker pool size (optional; auto when null)"
  default     = null
}

variable "agent_executor_cpu" {
  type    = string
  default = "4096"
}

variable "agent_executor_memory" {
  type    = string
  default = "8192"
}

variable "agent_executor_desired_count" {
  type        = number
  description = "Desired number of agent-executor instances to run"
  default     = 1
}

variable "agent_queue" {
  type        = string
  description = "Task queue for agent executor workers"
  default     = "shared-agent-queue"
}

variable "agent_executor_worker_pool_size" {
  type        = string
  description = "Agent executor worker pool size (optional; auto when null)"
  default     = null
}

variable "temporal_cpu" {
  type    = string
  default = "2048"
}

variable "temporal_memory" {
  type    = string
  default = "4096"
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
  default = "db.t4g.2xlarge"
}

variable "db_allocated_storage" {
  type    = string
  default = "5"
}

variable "db_engine_version" {
  type        = string
  description = "Exact Postgres engine version for the core RDS instance (e.g. 16.12). Override per workspace if a different minor is required."
  default     = "16.12"
}

### RDS Settings

variable "restore_from_snapshot" {
  type        = bool
  description = "Restore RDS instances from a snapshot"
  default     = false
}

variable "rds_multi_az" {
  type        = bool
  description = "Enable Multi-AZ for RDS instances"
  default     = false
}

variable "rds_skip_final_snapshot" {
  type        = bool
  description = "Skip final snapshot when deleting RDS instances"
  default     = false
}

variable "rds_deletion_protection" {
  type        = bool
  description = "Enable deletion protection for RDS instances"
  default     = true
}

variable "rds_apply_immediately" {
  type        = bool
  description = "Apply changes immediately to RDS instances"
  default     = true
}

variable "rds_backup_retention_period" {
  type        = number
  description = "The number of days to retain backups for RDS instances"
  default     = 7
}

variable "rds_performance_insights_enabled" {
  type        = bool
  description = "Enable Performance Insights for RDS instances"
  default     = false
}

variable "rds_database_insights_mode" {
  type        = string
  description = "The database insights mode for RDS instances (standard, advanced)"
  default     = "standard"
}

variable "rds_auto_minor_version_upgrade" {
  type        = bool
  description = "Enable auto minor version upgrades for RDS instances"
  default     = false
}

variable "core_db_snapshot_name" {
  type        = string
  description = "(Optional) Exact snapshot identifier to use when restoring the core database"
  default     = null
}

variable "temporal_db_snapshot_name" {
  type        = string
  description = "(Optional) Exact snapshot identifier to use when restoring the temporal database"
  default     = null
}

variable "sentry_dsn" {
  description = "The Sentry DSN to use for error reporting"
  type        = string
  default     = null
  sensitive   = true
}

# Redis Configuration

variable "redis_node_type" {
  type        = string
  description = "ElastiCache Redis node type"
  default     = "cache.t4g.micro"
}
