# Get current caller identity and region
data "aws_caller_identity" "current" {}

# Common assume role policy document
data "aws_iam_policy_document" "assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    effect  = "Allow"
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ECS Poll policy
resource "aws_iam_policy" "ecs_poll" {
  name        = "TracecatECSPollPolicy"
  description = "Policy for ECS Poll action"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ecs:Poll"]
        Resource = [
          "arn:aws:ecs:*:${data.aws_caller_identity.current.account_id}:task-set/cluster/*"
        ]
      }
    ]
  })
}


# Redis IAM access policy
resource "aws_iam_policy" "redis_iam_access" {
  name        = "TracecatRedisIAMAccessPolicy"
  description = "Policy for ElastiCache Redis access (SG-only, no IAM auth)"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "elasticache:DescribeReplicationGroups"
        ]
        Resource = "*"
      }
    ]
  })
}

# S3 access policy for blob storage
resource "aws_iam_policy" "s3_attachments_access" {
  name        = "TracecatS3BlobStoragePolicy"
  description = "Policy for S3 blob storage access with security restrictions"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowBlobStorageOperations"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:PutObjectTagging",
          "s3:GetObjectTagging",
          "s3:HeadObject"
        ]
        Resource = [
          "${aws_s3_bucket.attachments.arn}/*"
        ]
        Condition = {
          StringEquals = {
            "s3:x-amz-server-side-encryption" = "AES256"
          }
        }
      },
      {
        Sid    = "AllowBucketOperations"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:HeadBucket"
        ]
        Resource = [
          aws_s3_bucket.attachments.arn
        ]
        Condition = {
          StringLike = {
            "s3:prefix" = ["*"]
          }
        }
      },
      {
        Sid    = "AllowPresignedURLGeneration"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = [
          "${aws_s3_bucket.attachments.arn}/*"
        ]
        Condition = {
          StringEquals = {
            "s3:ExistingObjectTag/AccessControlled" = "true"
          }
          StringLike = {
            "aws:userid" = "${aws_iam_role.api_worker_task.unique_id}:*"
          }
        }
      }
    ]
  })
}

# S3 access policy for registry storage (read-only, only for non-legacy executor)
resource "aws_iam_policy" "s3_registry_access" {
  count       = var.use_legacy_executor ? 0 : 1
  name        = "TracecatS3RegistryStoragePolicy"
  description = "Policy for S3 registry storage read access"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowRegistryReadOperations"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:HeadObject"
        ]
        Resource = [
          "${aws_s3_bucket.registry[0].arn}/*"
        ]
      },
      {
        Sid    = "AllowRegistryBucketOperations"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:HeadBucket"
        ]
        Resource = [
          aws_s3_bucket.registry[0].arn
        ]
      }
    ]
  })
}

# Secrets access policy
resource "aws_iam_policy" "secrets_access" {
  name        = "TracecatSecretsAccessPolicy"
  description = "Policy for accessing Tracecat secrets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = compact([
          var.tracecat_db_encryption_key_arn,
          var.tracecat_service_key_arn,
          var.tracecat_signing_secret_arn,
          var.oauth_client_id_arn,
          var.oauth_client_secret_arn,
          var.saml_idp_metadata_url_arn,
        ])
      }
    ]
  })

  depends_on = [aws_db_instance.core_database]
}

resource "aws_iam_policy" "ui_secrets_access" {
  name        = "TracecatUISecretsAccessPolicy"
  description = "Policy for accessing Tracecat UI secrets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = compact([
          var.tracecat_service_key_arn,
        ])
      }
    ]
  })
}

resource "aws_iam_policy" "task_secrets_access" {
  # Enable this policy if temporal autosetup is disabled
  count       = var.disable_temporal_autosetup ? 1 : 0
  name        = "TracecatTaskSecretsAccessPolicy"
  description = "Policy for accessing Tracecat secrets at runtime"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = compact([
          var.temporal_api_key_arn,
        ])
      }
    ]
  })
}

resource "aws_iam_policy" "temporal_secrets_access" {
  count       = var.disable_temporal_autosetup ? 0 : 1
  name        = "TracecatTemporalSecretsAccessPolicy"
  description = "Policy for accessing Temporal secrets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_db_instance.temporal_database[0].master_user_secret[0].secret_arn
        ]
      }
    ]
  })

  depends_on = [aws_db_instance.temporal_database]
}

# API execution role
resource "aws_iam_role" "api_execution" {
  name               = "TracecatAPIExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy_attachment" "api_execution_ecs_poll" {
  policy_arn = aws_iam_policy.ecs_poll.arn
  role       = aws_iam_role.api_execution.name
}

resource "aws_iam_role_policy_attachment" "api_execution_secrets" {
  policy_arn = aws_iam_policy.secrets_access.arn
  role       = aws_iam_role.api_execution.name
}

# Worker execution role
resource "aws_iam_role" "worker_execution" {
  name               = "TracecatWorkerExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy_attachment" "worker_execution_ecs_poll" {
  policy_arn = aws_iam_policy.ecs_poll.arn
  role       = aws_iam_role.worker_execution.name
}

resource "aws_iam_role_policy_attachment" "worker_execution_secrets" {
  policy_arn = aws_iam_policy.secrets_access.arn
  role       = aws_iam_role.worker_execution.name
}

# API and Worker task role
resource "aws_iam_role" "api_worker_task" {
  name               = "TracecatAPIWorkerTaskRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy" "api_worker_task_db_access" {
  name = "TracecatAPIWorkerDBAccessPolicy"
  role = aws_iam_role.api_worker_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds-db:connect",
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          "${aws_db_instance.core_database.arn}/postgres",
          aws_db_instance.core_database.master_user_secret[0].secret_arn,
        ]
      }
    ]
  })
}
# Attach S3 policy to API/Worker task role
resource "aws_iam_role_policy_attachment" "api_worker_task_s3" {
  policy_arn = aws_iam_policy.s3_attachments_access.arn
  role       = aws_iam_role.api_worker_task.name
}

# Attach S3 registry policy to API/Worker task role (only for non-legacy executor)
resource "aws_iam_role_policy_attachment" "api_worker_task_s3_registry" {
  count      = var.use_legacy_executor ? 0 : 1
  policy_arn = aws_iam_policy.s3_registry_access[0].arn
  role       = aws_iam_role.api_worker_task.name
}

# Attach Redis IAM policy to API/Worker task role
resource "aws_iam_role_policy_attachment" "api_worker_task_redis" {
  policy_arn = aws_iam_policy.redis_iam_access.arn
  role       = aws_iam_role.api_worker_task.name
}

resource "aws_iam_role_policy_attachment" "api_worker_task_secrets" {
  # Enable this policy if temporal autosetup is disabled
  count      = var.disable_temporal_autosetup ? 1 : 0
  policy_arn = aws_iam_policy.task_secrets_access[0].arn
  role       = aws_iam_role.api_worker_task.name
}

# UI execution role
resource "aws_iam_role" "ui_execution" {
  name               = "TracecatUIExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy_attachment" "ui_execution_ecs_poll" {
  policy_arn = aws_iam_policy.ecs_poll.arn
  role       = aws_iam_role.ui_execution.name
}

# Add this new attachment for secrets access
resource "aws_iam_role_policy_attachment" "ui_execution_secrets" {
  policy_arn = aws_iam_policy.ui_secrets_access.arn
  role       = aws_iam_role.ui_execution.name
}

# Temporal execution role
resource "aws_iam_role" "temporal_execution" {
  count              = var.disable_temporal_autosetup ? 0 : 1
  name               = "TracecatTemporalExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy_attachment" "temporal_execution_ecs_poll" {
  count      = var.disable_temporal_autosetup ? 0 : 1
  policy_arn = aws_iam_policy.ecs_poll.arn
  role       = aws_iam_role.temporal_execution[0].name
}

resource "aws_iam_role_policy_attachment" "temporal_execution_secrets" {
  count      = var.disable_temporal_autosetup ? 0 : 1
  policy_arn = aws_iam_policy.temporal_secrets_access[0].arn
  role       = aws_iam_role.temporal_execution[0].name
}

# Temporal task role
resource "aws_iam_role" "temporal_task" {
  count              = var.disable_temporal_autosetup ? 0 : 1
  name               = "TracecatTemporalTaskRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy" "temporal_task_db_access" {
  count = var.disable_temporal_autosetup ? 0 : 1
  name  = "TracecatTemporalDBAccessPolicy"
  role  = aws_iam_role.temporal_task[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds-db:connect"
        ]
        Resource = [
          "${aws_db_instance.temporal_database[0].arn}/postgres"
        ]
      }
    ]
  })
}

# Caddy execution role
resource "aws_iam_role" "caddy_execution" {
  name               = "TracecatCaddyExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy_attachment" "caddy_execution_ecs_poll" {
  policy_arn = aws_iam_policy.ecs_poll.arn
  role       = aws_iam_role.caddy_execution.name
}

# Add CloudWatch Logs policy
resource "aws_iam_policy" "cloudwatch_logs" {
  name        = "TracecatCloudWatchLogsPolicy"
  description = "Policy for writing to CloudWatch Logs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.tracecat_log_group.arn}:*"
      }
    ]
  })
}

# Attach CloudWatch Logs policy to execution roles

resource "aws_iam_role_policy_attachment" "api_execution_cloudwatch_logs" {
  policy_arn = aws_iam_policy.cloudwatch_logs.arn
  role       = aws_iam_role.api_execution.name
}

resource "aws_iam_role_policy_attachment" "worker_execution_cloudwatch_logs" {
  policy_arn = aws_iam_policy.cloudwatch_logs.arn
  role       = aws_iam_role.worker_execution.name
}

resource "aws_iam_role_policy_attachment" "ui_execution_cloudwatch_logs" {
  policy_arn = aws_iam_policy.cloudwatch_logs.arn
  role       = aws_iam_role.ui_execution.name
}

resource "aws_iam_role_policy_attachment" "temporal_execution_cloudwatch_logs" {
  count      = var.disable_temporal_autosetup ? 0 : 1
  policy_arn = aws_iam_policy.cloudwatch_logs.arn
  role       = aws_iam_role.temporal_execution[0].name
}

resource "aws_iam_role_policy_attachment" "caddy_execution_cloudwatch_logs" {
  policy_arn = aws_iam_policy.cloudwatch_logs.arn
  role       = aws_iam_role.caddy_execution.name
}

# (Optional) Temporal UI execution role
resource "aws_iam_policy" "temporal_ui_secrets_access" {
  count       = var.disable_temporal_ui ? 0 : 1
  name        = "TracecatTemporalUISecretsAccessPolicy"
  description = "Policy for accessing Temporal UI secrets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = compact([
          var.temporal_auth_client_id_arn,
          var.temporal_auth_client_secret_arn
        ])
      }
    ]
  })
}

resource "aws_iam_role" "temporal_ui_execution" {
  count              = var.disable_temporal_ui ? 0 : 1
  name               = "TracecatTemporalUIExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy_attachment" "temporal_ui_execution_ecs_poll" {
  count      = var.disable_temporal_ui ? 0 : 1
  policy_arn = aws_iam_policy.ecs_poll.arn
  role       = aws_iam_role.temporal_ui_execution[0].name
}

resource "aws_iam_role_policy_attachment" "temporal_ui_execution_cloudwatch_logs" {
  count      = var.disable_temporal_ui ? 0 : 1
  policy_arn = aws_iam_policy.cloudwatch_logs.arn
  role       = aws_iam_role.temporal_ui_execution[0].name
}

resource "aws_iam_role_policy_attachment" "temporal_ui_execution_secrets" {
  count      = var.disable_temporal_ui ? 0 : 1
  policy_arn = aws_iam_policy.temporal_ui_secrets_access[0].arn
  role       = aws_iam_role.temporal_ui_execution[0].name
}

# Temporal UI task role (minimal permissions)
resource "aws_iam_role" "temporal_ui_task" {
  count              = var.disable_temporal_ui ? 0 : 1
  name               = "TracecatTemporalUITaskRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}
