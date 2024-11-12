## AWS provider variables

variable "aws_region" {
  type        = string
  description = "AWS region (secrets and hosted zone must be in the same region)"
}

variable "az_count" {
  type        = number
  description = "Number of AZs to cover in a given region"
  default     = 2
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

### Images and Versions

variable "temporal_server_image" {
  type    = string
  default = "temporalio/auto-setup"
}

variable "temporal_server_image_tag" {
  type    = string
  default = "1.24.2"
}

variable "tracecat_image" {
  type    = string
  default = "ghcr.io/tracecathq/tracecat"
}

variable "tracecat_ui_image" {
  type    = string
  default = "ghcr.io/tracecathq/tracecat-ui"
}

variable "TFC_CONFIGURATION_VERSION_GIT_COMMIT_SHA" {
  description = "Terraform Cloud only: the git commit SHA of that triggered the run"
  type        = string
  default     = null
}

variable "tracecat_image_tag" {
  type    = string
  default = "0.13.1"
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

### DNS

variable "hosted_zone_id" {
  type        = string
  description = "The ID of the hosted zone in Route53"
}

variable "domain_name" {
  type        = string
  description = "The domain name to use for the application"
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

variable "rds_auto_minor_version_upgrade" {
  type        = bool
  description = "Enable auto minor version upgrades for RDS instances"
  default     = false
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
  default     = "INFO"
}

variable "tracecat_app_env" {
  type        = string
  description = "The environment of the Tracecat application"
  default     = "production"
}

# UI

variable "auth_types" {
  type    = string
  default = "basic,google_oauth"
}
