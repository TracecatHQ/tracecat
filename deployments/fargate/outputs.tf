# =============================================================================
# ENDPOINTS - Key URLs for accessing the application
# =============================================================================

output "tracecat_endpoints" {
  description = "Key endpoints for accessing the Tracecat UI and API"
  value = {
    ui_url      = module.ecs.public_app_url
    api_url     = module.ecs.public_api_url
    domain_name = var.domain_name
  }
}

# =============================================================================
# TRACECAT APPLICATION OUTPUTS
# =============================================================================

output "tracecat_image_tag" {
  description = "The version of Tracecat used"
  value       = module.ecs.tracecat_image_tag
}

output "public_app_url" {
  description = "The public URL of the Tracecat application"
  value       = module.ecs.public_app_url
}

output "public_api_url" {
  description = "The public URL of the Tracecat API"
  value       = module.ecs.public_api_url
}

output "internal_api_url" {
  description = "The internal URL of the API (for service-to-service communication)"
  value       = module.ecs.internal_api_url
}

output "allow_origins" {
  description = "The allowed origins for CORS"
  value       = module.ecs.allow_origins
}

# =============================================================================
# INFRASTRUCTURE OUTPUTS
# =============================================================================

output "vpc_id" {
  description = "The ID of the VPC"
  value       = module.network.vpc_id
}

output "public_subnet_ids" {
  description = "The IDs of the public subnets"
  value       = module.network.public_subnet_ids
}

output "private_subnet_ids" {
  description = "The IDs of the private subnets"
  value       = module.network.private_subnet_ids
}

output "private_route_table_ids" {
  description = "The IDs of the private route tables"
  value       = module.network.private_route_table_ids
}

output "acm_certificate_arn" {
  description = "The ARN of the ACM certificate"
  value       = module.network.acm_certificate_arn
}

output "local_dns_namespace" {
  description = "The local DNS namespace for ECS services"
  value       = module.ecs.local_dns_namespace
}

# =============================================================================
# STORAGE OUTPUTS
# =============================================================================

output "s3_attachments_bucket_name" {
  description = "The name of the S3 bucket used for attachments storage"
  value       = module.ecs.s3_attachments_bucket_name
}

output "s3_attachments_bucket_arn" {
  description = "The ARN of the S3 bucket used for attachments storage"
  value       = module.ecs.s3_attachments_bucket_arn
}

output "s3_registry_bucket_name" {
  description = "The name of the S3 bucket used for registry storage"
  value       = module.ecs.s3_registry_bucket_name
}

output "s3_registry_bucket_arn" {
  description = "The ARN of the S3 bucket used for registry storage"
  value       = module.ecs.s3_registry_bucket_arn
}

# =============================================================================
# DATABASE OUTPUTS
# =============================================================================

output "core_db_endpoint" {
  description = "The endpoint of the core database (hostname:port, no credentials)"
  value       = module.ecs.core_db_endpoint
}

output "core_db_port" {
  description = "The port of the core database"
  value       = module.ecs.core_db_port
}

output "temporal_db_endpoint" {
  description = "The endpoint of the temporal database (hostname:port, no credentials)"
  value       = module.ecs.temporal_db_endpoint
}

output "temporal_db_port" {
  description = "The port of the temporal database"
  value       = module.ecs.temporal_db_port
}

output "latest_core_snapshot_encrypted" {
  description = "Whether the latest core database snapshot is encrypted (only available when using automatic snapshot selection)"
  value       = module.ecs.latest_core_snapshot_encrypted
}

output "latest_temporal_snapshot_encrypted" {
  description = "Whether the latest temporal database snapshot is encrypted (only available when using automatic snapshot selection)"
  value       = module.ecs.latest_temporal_snapshot_encrypted
} 