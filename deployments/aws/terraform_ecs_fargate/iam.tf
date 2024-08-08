data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

resource "aws_iam_policy" "default_execution_role_policy" {
  name        = "DefaultExecutionRolePolicy"
  path        = "/"
  description = "Default policy for ECS execution roles"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecs:Poll"]
        Resource = ["arn:${data.aws_partition.current.partition}:ecs:*:${data.aws_caller_identity.current.account_id}:task-set/cluster/*"]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution_role_attachment" {
  policy_arn = aws_iam_policy.default_execution_role_policy.arn
  role       = aws_iam_role.ecs_execution_role.name
}

resource "aws_iam_role_policy_attachment" "api_execution_role_attachment" {
  policy_arn = aws_iam_policy.default_execution_role_policy.arn
  role       = aws_iam_role.api_execution_role.name
}

resource "aws_iam_role_policy_attachment" "worker_execution_role_attachment" {
  policy_arn = aws_iam_policy.default_execution_role_policy.arn
  role       = aws_iam_role.worker_execution_role.name
}

/*resource "aws_iam_role_policy_attachment" "ui_execution_role_attachment" {
  policy_arn = aws_iam_policy.default_execution_role_policy.arn
  role       = aws_iam_role.ui_execution_role.name
}*/

/*resource "aws_iam_role_policy_attachment" "temporal_execution_role_attachment" {
  policy_arn = aws_iam_policy.default_execution_role_policy.arn
  role       = aws_iam_role.temporal_execution_role.name
}*/

resource "aws_iam_role_policy" "api_secrets_policy" {
  name = "TracecatApiSecretsPolicy"
  role = aws_iam_role.api_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_secretsmanager_secret.db_encryption_key.arn,
          aws_secretsmanager_secret.service_key.arn,
          aws_secretsmanager_secret.signing_secret.arn,
          aws_secretsmanager_secret.db_pass.arn,
          aws_secretsmanager_secret.postgres_pwd.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role" "api_execution_role" {
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

resource "aws_iam_role" "worker_execution_role" {
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

resource "aws_iam_role" "ecs_execution_role" {
  name = "ecs-execution-role"

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

resource "aws_iam_role" "ecs_task_role" {
  name = "TracecatApiFargateServiceTaskRole"

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

resource "aws_iam_role_policy" "ecs_permissions" {
  name = "ecs-permissions"
  role = aws_iam_role.ecs_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "secretsmanager:GetSecretValue",
        ]
        Effect = "Allow"
        Resource = [
          aws_secretsmanager_secret.db_encryption_key.arn,
          aws_secretsmanager_secret.service_key.arn,
          aws_secretsmanager_secret.signing_secret.arn,
          aws_secretsmanager_secret.db_pass.arn
        ]
      },
      {
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Effect = "Allow"
        Resource = "arn:aws:logs:*:*:log-group:/ecs/tracecat:*"
      }
    ]
  })
}

/*resource "aws_iam_role_policy" "ecs_permissions" {
  name = "ecs-secrets-access"
  role = aws_iam_role.ecs_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "secretsmanager:GetSecretValue",
        ]
        Effect = "Allow"
        Resource = [
          aws_secretsmanager_secret.db_encryption_key.arn,
          aws_secretsmanager_secret.service_key.arn,
          aws_secretsmanager_secret.signing_secret.arn,
          aws_secretsmanager_secret.db_pass.arn
        ]
      }
    ]
  })
}*/
