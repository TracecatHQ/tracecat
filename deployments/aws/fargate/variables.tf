## AWS provider variables

variable "aws_region" {
  default = "us-west-2"
}

variable "az_count" {
  description = "Number of AZs to cover in a given region"
  default     = "2"
}

### Images and Versions

variable "temporal_server_image" {
  default = "temporalio/auto-setup"
}

variable "temporal_server_image_tag" {
  #default = "latest"
  default = "1.24.2"
}

variable "tracecat_image" {
  default = "ghcr.io/tracecathq/tracecat"
}

variable "tracecat_ui_image" {
  default = "ghcr.io/tracecathq/tracecat-ui"
}

variable "tracecat_image_tag" {
  default = "0.8.5"
}

variable "force_new_deployment" {
  description = "Force a new deployment of Tracecat services. Used to update services with new images."
  type    = bool
  default = false
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
  description = "The OAuth client ID (optional)"
  default     = null
}

variable "oauth_client_secret_arn" {
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
  default     = "INFO"
}

variable "tracecat_app_env" {
  type        = string
  description = "The environment of the Tracecat application"
  default     = "production"
}

variable "tracecat_db_sslmode" {
  type        = string
  description = "SSL mode for the database connection"
  default     = "require"
}

# UI

variable "auth_types" {
  type    = string
  default = "basic,google_oauth"
}
