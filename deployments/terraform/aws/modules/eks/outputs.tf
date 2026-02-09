output "cluster_endpoint" {
  description = "Endpoint for the EKS cluster"
  value       = aws_eks_cluster.tracecat.endpoint
}

output "cluster_name" {
  description = "Name of the EKS cluster"
  value       = aws_eks_cluster.tracecat.name
}

output "cluster_certificate_authority_data" {
  description = "Base64 encoded certificate data for the EKS cluster"
  value       = aws_eks_cluster.tracecat.certificate_authority[0].data
}

output "cluster_security_group_id" {
  description = "Security group ID attached to the EKS cluster"
  value       = aws_security_group.eks_cluster.id
}

output "node_group_arn" {
  description = "ARN of the EKS node group"
  value       = aws_eks_node_group.tracecat.arn
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint"
  value       = aws_db_instance.tracecat.endpoint
}

output "rds_address" {
  description = "RDS PostgreSQL address (hostname only)"
  value       = aws_db_instance.tracecat.address
}

output "rds_identifier" {
  description = "RDS PostgreSQL instance identifier"
  value       = aws_db_instance.tracecat.identifier
}

output "elasticache_endpoint" {
  description = "ElastiCache Redis endpoint"
  value       = aws_elasticache_replication_group.tracecat.primary_endpoint_address
}

output "s3_attachments_bucket" {
  description = "S3 bucket name for attachments"
  value       = aws_s3_bucket.attachments.id
}

output "s3_registry_bucket" {
  description = "S3 bucket name for registry"
  value       = aws_s3_bucket.registry.id
}

output "tracecat_url" {
  description = "URL for accessing Tracecat"
  value       = "https://${var.domain_name}"
}

output "tracecat_namespace" {
  description = "Kubernetes namespace where Tracecat is deployed"
  value       = kubernetes_namespace.tracecat.metadata[0].name
}

output "cluster_auth_token" {
  description = "Authentication token for the EKS cluster"
  value       = data.aws_eks_cluster_auth.tracecat.token
  sensitive   = true
}

output "oidc_provider_arn" {
  description = "ARN of the OIDC provider for IRSA"
  value       = aws_iam_openid_connect_provider.eks.arn
}

output "s3_access_role_arn" {
  description = "ARN of the IAM role for S3 access (IRSA)"
  value       = aws_iam_role.tracecat_s3.arn
}

# Compliance Evidence Outputs

output "database_authentication" {
  description = "Database credential management and network access controls."
  value = {
    master_username            = aws_db_instance.tracecat.username
    managed_credentials        = aws_db_instance.tracecat.manage_master_user_password
    publicly_accessible        = aws_db_instance.tracecat.publicly_accessible
    password_rotation_schedule = var.rds_password_rotation_schedule
  }
}

output "encryption_at_rest" {
  description = "Encryption at rest for RDS, ElastiCache, and S3."
  value = {
    rds_storage_encrypted          = aws_db_instance.tracecat.storage_encrypted
    redis_encryption_enabled       = aws_elasticache_replication_group.tracecat.at_rest_encryption_enabled
    s3_attachments_sse_algorithm   = one(one(aws_s3_bucket_server_side_encryption_configuration.attachments.rule).apply_server_side_encryption_by_default).sse_algorithm
    s3_registry_sse_algorithm      = one(one(aws_s3_bucket_server_side_encryption_configuration.registry.rule).apply_server_side_encryption_by_default).sse_algorithm
    s3_workflow_sse_algorithm      = one(one(aws_s3_bucket_server_side_encryption_configuration.workflow.rule).apply_server_side_encryption_by_default).sse_algorithm
  }
}

output "encryption_in_transit" {
  description = "HTTPS enforcement and TLS for all service connections."
  value = {
    alb_http_to_https_redirect = local.tracecat_alb_http_to_https_redirect_enabled
    redis_tls_enabled          = aws_elasticache_replication_group.tracecat.transit_encryption_enabled
  }
}

output "performance_monitoring" {
  description = "RDS database performance monitoring configuration."
  value = {
    database_insights_mode       = aws_db_instance.tracecat.database_insights_mode
    performance_insights_enabled = aws_db_instance.tracecat.performance_insights_enabled
  }
}
