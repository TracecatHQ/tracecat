# Network Outputs
output "vpc_id" {
  description = "ID of the VPC"
  value       = module.network.vpc_id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets"
  value       = module.network.public_subnet_ids
}

output "private_subnet_ids" {
  description = "IDs of the private subnets"
  value       = module.network.private_subnet_ids
}

output "acm_certificate_arn" {
  description = "ARN of the ACM certificate"
  value       = module.network.acm_certificate_arn
}

output "nat_gateway_eips" {
  description = "Public Elastic IPs attached to NAT gateways for outbound allowlisting"
  value       = module.network.nat_gateway_eips
}

# EKS Outputs
output "cluster_endpoint" {
  description = "Endpoint for the EKS cluster"
  value       = module.eks.cluster_endpoint
}

output "cluster_name" {
  description = "Name of the EKS cluster"
  value       = module.eks.cluster_name
}

output "cluster_security_group_id" {
  description = "Security group ID attached to the EKS cluster"
  value       = module.eks.cluster_security_group_id
}

output "node_group_arn" {
  description = "ARN of the EKS node group"
  value       = module.eks.node_group_arn
}

# Data Services Outputs
output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint"
  value       = module.eks.rds_endpoint
}

output "rds_identifier" {
  description = "RDS PostgreSQL instance identifier"
  value       = module.eks.rds_identifier
}

output "elasticache_endpoint" {
  description = "ElastiCache Redis endpoint"
  value       = module.eks.elasticache_endpoint
}

output "s3_attachments_bucket" {
  description = "S3 bucket name for attachments"
  value       = module.eks.s3_attachments_bucket
}

output "s3_registry_bucket" {
  description = "S3 bucket name for registry"
  value       = module.eks.s3_registry_bucket
}

# Tracecat Outputs
output "tracecat_url" {
  description = "URL for accessing Tracecat"
  value       = module.eks.tracecat_url
}

output "tracecat_namespace" {
  description = "Kubernetes namespace where Tracecat is deployed"
  value       = module.eks.tracecat_namespace
}

# Kubeconfig Command
output "configure_kubectl" {
  description = "Command to configure kubectl for the EKS cluster"
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

# Compliance Evidence Outputs

output "database_authentication" {
  description = "Database credential management and network access controls."
  value       = module.eks.database_authentication
}

output "encryption_at_rest" {
  description = "Encryption at rest for RDS, ElastiCache, and S3."
  value       = module.eks.encryption_at_rest
}

output "encryption_in_transit" {
  description = "HTTPS enforcement and TLS for all service connections."
  value       = module.eks.encryption_in_transit
}

output "performance_monitoring" {
  description = "RDS database performance monitoring configuration."
  value       = module.eks.performance_monitoring
}

output "observability" {
  description = "Observability pipeline status"
  value       = module.eks.observability
}
