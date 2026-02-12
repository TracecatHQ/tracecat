resource "random_id" "attachments_bucket_suffix" {
  byte_length = 4
}

# Future bucket examples (when needed):
# resource "random_uuid" "audit_logs_bucket_guid" {}
# resource "aws_s3_bucket" "audit_logs" {
#   bucket = "tracecat-audit-logs-${var.tracecat_app_env}-${replace(random_uuid.audit_logs_bucket_guid.result, "-", "")}"
# }
#
# resource "random_uuid" "backups_bucket_guid" {}
# resource "aws_s3_bucket" "backups" {
#   bucket = "tracecat-backups-${var.tracecat_app_env}-${replace(random_uuid.backups_bucket_guid.result, "-", "")}"
# }

# S3 bucket for Tracecat case attachments
resource "aws_s3_bucket" "attachments" {
  bucket = "tracecat-attachments-${var.tracecat_app_env}-${random_id.attachments_bucket_suffix.hex}"

  tags = {
    Name        = "Tracecat attachments storage"
    Environment = var.tracecat_app_env
    Service     = "tracecat"
    Purpose     = "attachments"
    ManagedBy   = "terraform"
  }
}

# Block public access completely
resource "aws_s3_bucket_public_access_block" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable versioning for data protection
resource "aws_s3_bucket_versioning" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Server-side encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Lifecycle policy for cost optimization
resource "aws_s3_bucket_lifecycle_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  rule {
    id     = "attachments_lifecycle"
    status = "Enabled"

    # Apply to all objects in the bucket
    filter {}

    # Transition to IA after 30 days
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    # Transition to Glacier after 90 days
    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    # Delete non-current versions after 90 days
    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    # Clean up incomplete multipart uploads
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Bucket policy for secure access
resource "aws_s3_bucket_policy" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowECSTaskAccess"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.api_worker_task.arn
        }
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:PutObjectTagging",
          "s3:GetObjectTagging",
          "s3:ListBucket"
        ]
        Resource = [
          "${aws_s3_bucket.attachments.arn}/*",
          aws_s3_bucket.attachments.arn
        ]
      },
      {
        Sid    = "DenyInsecureConnections"
        Effect = "Deny"
        Principal = {
          AWS = aws_iam_role.api_worker_task.arn
        }
        Action = "s3:*"
        Resource = [
          aws_s3_bucket.attachments.arn,
          "${aws_s3_bucket.attachments.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

# CORS configuration for case attachments
resource "aws_s3_bucket_cors_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD", "POST", "PUT", "DELETE"]
    allowed_origins = ["https://${var.domain_name}"]
    expose_headers  = ["ETag", "Content-Type", "Content-Length", "Content-Disposition"]
    max_age_seconds = 3600
  }
}

# Registry bucket (only for non-legacy executor - 0.54.0+)
resource "random_id" "registry_bucket_suffix" {
  count       = var.use_legacy_executor ? 0 : 1
  byte_length = 4
}

# S3 bucket for Tracecat registry
resource "aws_s3_bucket" "registry" {
  count  = var.use_legacy_executor ? 0 : 1
  bucket = "tracecat-registry-${var.tracecat_app_env}-${random_id.registry_bucket_suffix[0].hex}"

  tags = {
    Name        = "Tracecat registry storage"
    Environment = var.tracecat_app_env
    Service     = "tracecat"
    Purpose     = "registry"
    ManagedBy   = "terraform"
  }
}

# Block public access completely
resource "aws_s3_bucket_public_access_block" "registry" {
  count  = var.use_legacy_executor ? 0 : 1
  bucket = aws_s3_bucket.registry[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable versioning for data protection
resource "aws_s3_bucket_versioning" "registry" {
  count  = var.use_legacy_executor ? 0 : 1
  bucket = aws_s3_bucket.registry[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

# Server-side encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "registry" {
  count  = var.use_legacy_executor ? 0 : 1
  bucket = aws_s3_bucket.registry[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Lifecycle policy for cost optimization
resource "aws_s3_bucket_lifecycle_configuration" "registry" {
  count  = var.use_legacy_executor ? 0 : 1
  bucket = aws_s3_bucket.registry[0].id

  rule {
    id     = "registry_lifecycle"
    status = "Enabled"

    # Apply to all objects in the bucket
    filter {}

    # Transition to IA after 30 days
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    # Transition to Glacier after 90 days
    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    # Delete non-current versions after 90 days
    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    # Clean up incomplete multipart uploads
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Bucket policy for secure access (read-only for executor)
resource "aws_s3_bucket_policy" "registry" {
  count  = var.use_legacy_executor ? 0 : 1
  bucket = aws_s3_bucket.registry[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowECSTaskAccess"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.api_worker_task.arn
        }
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          "${aws_s3_bucket.registry[0].arn}/*",
          aws_s3_bucket.registry[0].arn
        ]
      },
      {
        Sid    = "DenyInsecureConnections"
        Effect = "Deny"
        Principal = {
          AWS = aws_iam_role.api_worker_task.arn
        }
        Action = "s3:*"
        Resource = [
          aws_s3_bucket.registry[0].arn,
          "${aws_s3_bucket.registry[0].arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}
