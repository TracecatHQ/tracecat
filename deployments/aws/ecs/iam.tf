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

resource "aws_iam_policy" "temporal_secrets_access" {
  name        = "TracecatTemporalSecretsAccessPolicy"
  description = "Policy for accessing Temporal secrets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_db_instance.temporal_database.master_user_secret[0].secret_arn
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

# Executor execution role
resource "aws_iam_role" "executor_execution" {
  name               = "TracecatExecutorExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
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

resource "aws_iam_role_policy_attachment" "temporal_execution_secrets" {
  policy_arn = aws_iam_policy.temporal_secrets_access.arn
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
          "${aws_db_instance.temporal_database.arn}/postgres"
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

# Caddy task role (minimal permissions)
resource "aws_iam_role" "caddy_task" {
  name               = "TracecatCaddyTaskRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
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
  policy_arn = aws_iam_policy.cloudwatch_logs.arn
  role       = aws_iam_role.temporal_execution.name
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
        Resource = [
          var.temporal_auth_client_id_arn,
          var.temporal_auth_client_secret_arn
        ]
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
