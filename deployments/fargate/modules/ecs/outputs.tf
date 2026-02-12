output "tracecat_image_tag" {
  description = "The version of Tracecat used"
  value       = local.tracecat_image_tag
}

output "public_app_url" {
  description = "The public URL of the app"
  value       = local.public_app_url
}

output "public_api_url" {
  description = "The public URL of the API"
  value       = local.public_api_url
}

output "internal_api_url" {
  description = "The internal URL of the API"
  value       = local.internal_api_url
}

output "allow_origins" {
  description = "The allowed origins for CORS"
  value       = local.allow_origins
}

output "local_dns_namespace" {
  description = "The local DNS namespace for ECS services"
  value       = local.local_dns_namespace
}

# Database outputs

output "core_db_endpoint" {
  description = "The endpoint of the core database (hostname only, no credentials)"
  value       = aws_db_instance.core_database.endpoint
}

output "core_db_port" {
  description = "The port of the core database"
  value       = aws_db_instance.core_database.port
}

output "temporal_db_endpoint" {
  description = "The endpoint of the temporal database (hostname only, no credentials)"
  value       = var.disable_temporal_autosetup ? null : aws_db_instance.temporal_database[0].endpoint
}

output "temporal_db_port" {
  description = "The port of the temporal database"
  value       = var.disable_temporal_autosetup ? null : aws_db_instance.temporal_database[0].port
}

# Existing outputs

output "latest_core_snapshot_encrypted" {
  value       = var.restore_from_snapshot && var.core_db_snapshot_name == null ? try(data.aws_db_snapshot.core_snapshots[0].encrypted, null) : null
  description = "Whether the latest core database snapshot is encrypted (only available when using automatic snapshot selection)"
}

output "latest_temporal_snapshot_encrypted" {
  value       = var.restore_from_snapshot && var.temporal_db_snapshot_name == null && !var.disable_temporal_autosetup ? try(data.aws_db_snapshot.temporal_snapshots[0].encrypted, null) : null
  description = "Whether the latest temporal database snapshot is encrypted (only available when using automatic snapshot selection)"
}

output "s3_attachments_bucket_name" {
  value       = aws_s3_bucket.attachments.bucket
  description = "The name of the S3 bucket used for attachments storage"
}

output "s3_attachments_bucket_arn" {
  value       = aws_s3_bucket.attachments.arn
  description = "The ARN of the S3 bucket used for attachments storage"
}

output "s3_registry_bucket_name" {
  value       = var.use_legacy_executor ? null : aws_s3_bucket.registry[0].bucket
  description = "The name of the S3 bucket used for registry storage (null when use_legacy_executor is true)"
}

output "s3_registry_bucket_arn" {
  value       = var.use_legacy_executor ? null : aws_s3_bucket.registry[0].arn
  description = "The ARN of the S3 bucket used for registry storage (null when use_legacy_executor is true)"
}

# Redis outputs

output "redis_endpoint" {
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
  description = "The primary endpoint address of the Redis cluster"
}

output "redis_port" {
  value       = aws_elasticache_replication_group.redis.port
  description = "The port of the Redis cluster"
}

output "redis_url" {
  value       = local.redis_url
  description = "The Redis connection URL with IAM authentication"
}

output "core_sg_id" {
  value       = aws_security_group.core.id
  description = "The ID of the core security group"
}

output "caddy_sg_id" {
  value       = aws_security_group.caddy.id
  description = "The ID of the caddy security group"
}
