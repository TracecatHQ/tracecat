## AWS provider variables

variable "aws_region" {
  type        = string
  description = "AWS region (secrets and hosted zone must be in the same region)"
}

### DNS

variable "domain_name" {
  type        = string
  description = "The domain name to use for the application"
}

variable "hosted_zone_id" {
  type        = string
  description = "The ID of the hosted zone in Route53"
}

### Security

variable "auth_types" {
  type    = string
  default = "google_oauth,sso"
}


variable "auth_allowed_domains" {
  type        = string
  description = "Comma separated list of allowed domains for authentication (e.g. `acme.com,acme.ai`)"
  default     = null
}

### Images and Versions

variable "TFC_CONFIGURATION_VERSION_GIT_COMMIT_SHA" {
  description = "Terraform Cloud only: the git commit SHA of that triggered the run"
  type        = string
  default     = null
}

variable "tracecat_image_tag" {
  type    = string
  default = "0.15.3"
}

variable "use_git_commit_sha" {
  type        = bool
  description = "Use the git commit SHA as the image tag"
  default     = false
}

variable "force_new_deployment" {
  type        = bool
  description = "Force a new deployment of Tracecat services. Used to update services with new images."
  default     = false
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

variable "saml_idp_entity_id_arn" {
  type        = string
  description = "The ARN of the secret containing the SAML IDP entity ID (optional)"
  default     = null
}

variable "saml_idp_redirect_url_arn" {
  type        = string
  description = "The ARN of the secret containing the SAML IDP redirect URL (optional)"
  default     = null
}

variable "saml_idp_certificate_arn" {
  type        = string
  description = "The ARN of the secret containing the SAML IDP certificate (optional)"
  default     = null
}

variable "saml_idp_metadata_url_arn" {
  type        = string
  description = "The ARN of the secret containing the SAML IDP metadata URL (optional)"
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
  default = "256"
}

variable "worker_memory" {
  type    = string
  default = "512"
}

variable "ui_cpu" {
  type    = string
  default = "256"
}

variable "ui_memory" {
  type    = string
  default = "512"
}

variable "temporal_cpu" {
  type    = string
  default = "256"
}

variable "temporal_memory" {
  type    = string
  default = "512"
}

variable "temporal_client_rpc_timeout" {
  type        = string
  description = "RPC timeout for Temporal client in seconds"
  default     = null
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
