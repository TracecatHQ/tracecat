# Fetch current AWS region and account
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
data "aws_vpc" "selected" {
  id = var.vpc_id
}

# Fetch AWS RDS CA certificate bundle for TLS verification
# https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html
data "http" "rds_ca_bundle" {
  url = "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"
}

locals {
  aws_region     = data.aws_region.current.name
  aws_account_id = data.aws_caller_identity.current.account_id

  # RDS identifier suffix to avoid clashes during snapshot restore
  rds_suffix = random_id.rds_suffix.hex

  # RDS master secret ARN (managed by AWS)
  rds_master_secret_arn = aws_db_instance.tracecat.master_user_secret[0].secret_arn

  # Secrets synced by ESO
  external_secrets_secret_arns = compact([
    var.tracecat_secrets_arn,
    local.rds_master_secret_arn,
    aws_secretsmanager_secret.redis_url.arn,
    var.temporal_secret_arn,
  ])

  # External Secrets Operator settings
  external_secrets_store_name = "tracecat-aws-secrets"

  # Tracecat service account name for IRSA (matches Helm release defaults)
  tracecat_service_account_name = "tracecat-app"

  # S3 bucket names (using random suffix instead of account ID for security)
  s3_suffix             = random_id.s3_suffix.hex
  s3_attachments_bucket = "tracecat-attachments-${local.s3_suffix}"
  s3_registry_bucket    = "tracecat-registry-${local.s3_suffix}"
  s3_workflow_bucket    = "tracecat-workflow-${local.s3_suffix}"

  # Common labels for Kubernetes resources
  common_labels = {
    "app.kubernetes.io/managed-by" = "terraform"
    "app.kubernetes.io/part-of"    = "tracecat"
  }
}

# Generate random suffix for RDS identifier to avoid clashes during snapshot restore
resource "random_id" "rds_suffix" {
  byte_length = 4
}

# Generate random suffix for S3 bucket names to ensure global uniqueness
resource "random_id" "s3_suffix" {
  byte_length = 4
}
