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
        Resource = [
          var.tracecat_db_password_arn,
          var.tracecat_db_encryption_key_arn,
          var.tracecat_service_key_arn,
          var.tracecat_signing_secret_arn,
          var.oauth_client_id_arn,
          var.oauth_client_secret_arn
        ]
      }
    ]
  })
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

# UI execution role
resource "aws_iam_role" "ui_execution" {
  name               = "TracecatUIExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy_attachment" "ui_execution_ecs_poll" {
  policy_arn = aws_iam_policy.ecs_poll.arn
  role       = aws_iam_role.ui_execution.name
}

# Temporal execution role
resource "aws_iam_role" "temporal_execution" {
  name               = "TracecatTemporalExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy_attachment" "temporal_execution_ecs_poll" {
  policy_arn = aws_iam_policy.ecs_poll.arn
  role       = aws_iam_role.temporal_execution.name
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
          "rds-db:connect"
        ]
        Resource = [
          "arn:aws:rds-db:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_db_instance.core_database.resource_id}/postgres"
        ]
      }
    ]
  })
}

# Temporal task role
resource "aws_iam_role" "temporal_task" {
  name               = "TracecatTemporalTaskRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy" "temporal_task_db_access" {
  name = "TracecatTemporalDBAccessPolicy"
  role = aws_iam_role.temporal_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "rds-db:connect"
        ]
        Resource = [
          "arn:aws:rds-db:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_db_instance.temporal_database.resource_id}/postgres"
        ]
      }
    ]
  })
}