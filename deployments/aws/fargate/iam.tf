data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

# Default ECS execution role policy
resource "aws_iam_policy" "ecs" {
  name        = "TracecatECSPolicy"
  description = "Default policy for ECS execution roles"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecs:Poll"]
        Resource = ["arn:${data.aws_partition.current.partition}:ecs:*:${data.aws_caller_identity.current.account_id}:task-set/cluster/*"]
      },
      {
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Effect   = "Allow"
        Resource = "arn:aws:logs:*:*:log-group:/ecs/tracecat:*"
      }
    ]
  })
}

# Secrets policy
locals {
  oauth_client_id_arn     = var.oauth_client_id != null ? aws_secretsmanager_secret.oauth_client_id[0].arn : null
  oauth_client_secret_arn = var.oauth_client_secret != null ? aws_secretsmanager_secret.oauth_client_secret[0].arn : null

  secret_arns = compact([
    var.db_encryption_arn.arn,
    var.db_pass.arn,
    var.postgres_pwd.arn,
    var.service_key.arn,
    var.signing_secret.arn,
    local.oauth_client_id_arn,
    local.oauth_client_secret_arn
  ])
}

resource "aws_iam_policy" "tracecat_secrets" {
  name        = "TracecatSecretsPolicy"
  description = "Policy for accessing Tracecat secrets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["secretsmanager:GetSecretValue"]
        Resource  = local.secret_arns
      }
    ]
  })
}

# Default ECS execution role
resource "aws_iam_role" "ecs" {
  name = "TracecatECSExecutionRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

# API execution role
resource "aws_iam_role" "api" {
  name = "TracecatApiExecutionRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

# Worker execution role
resource "aws_iam_role" "worker" {
  name = "TracecatWorkerExecutionRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

# Policy attachments
resource "aws_iam_role_policy_attachment" "ecs_default" {
  policy_arn = aws_iam_policy.ecs.arn
  role       = aws_iam_role.ecs.name
}

resource "aws_iam_role_policy_attachment" "api_ecs" {
  policy_arn = aws_iam_policy.ecs.arn
  role       = aws_iam_role.api.name
}

resource "aws_iam_role_policy_attachment" "api_secrets" {
  policy_arn = aws_iam_policy.tracecat_secrets.arn
  role       = aws_iam_role.api.name
}

resource "aws_iam_role_policy_attachment" "worker_ecs" {
  policy_arn = aws_iam_policy.ecs.arn
  role       = aws_iam_role.worker.name
}

resource "aws_iam_role_policy_attachment" "worker_secrets" {
  policy_arn = aws_iam_policy.tracecat_secrets.arn
  role       = aws_iam_role.worker.name
}