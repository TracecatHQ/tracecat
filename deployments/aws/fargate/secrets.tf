# Required secrets:
# 1. TRACECAT__DB_PASSWORD
# 2. TEMPORAL__DB_PASSWORD
# 3. TRACECAT__DB_ENCRYPTION_KEY
# 4. TRACECAT__SERVICE_KEY
# 5. TRACECAT__SIGNING_SECRET
#
# Optional secrets:
# 1. OAUTH_CLIENT_ID
# 2. OAUTH_CLIENT_SECRET

# Required secrets
data "aws_secretsmanager_secret" "tracecat_db_password" {
  arn = var.tracecat_db_password_arn
}

data "aws_secretsmanager_secret" "temporal_db_password" {
  arn = var.temporal_db_password_arn
}

data "aws_secretsmanager_secret" "tracecat_db_encryption_key" {
  arn = var.tracecat_db_encryption_key_arn
}

data "aws_secretsmanager_secret" "tracecat_service_key" {
  arn = var.tracecat_service_key_arn
}

data "aws_secretsmanager_secret" "tracecat_signing_secret" {
  arn = var.tracecat_signing_secret_arn
}

# Optional secrets
data "aws_secretsmanager_secret" "oauth_client_id" {
  count = var.oauth_client_id_arn != null ? 1 : 0
  arn   = var.oauth_client_id_arn
}

data "aws_secretsmanager_secret" "oauth_client_secret" {
  count = var.oauth_client_secret_arn != null ? 1 : 0
  arn   = var.oauth_client_secret_arn
}

# Retrieve secret values
data "aws_secretsmanager_secret_version" "tracecat_db_password" {
  secret_id = data.aws_secretsmanager_secret.tracecat_db_password.id
}

data "aws_secretsmanager_secret_version" "temporal_db_password" {
  secret_id = data.aws_secretsmanager_secret.temporal_db_password.id
}

data "aws_secretsmanager_secret_version" "tracecat_db_encryption_key" {
  secret_id = data.aws_secretsmanager_secret.tracecat_db_encryption_key.id
}

data "aws_secretsmanager_secret_version" "tracecat_service_key" {
  secret_id = data.aws_secretsmanager_secret.tracecat_service_key.id
}

data "aws_secretsmanager_secret_version" "tracecat_signing_secret" {
  secret_id = data.aws_secretsmanager_secret.tracecat_signing_secret.id
}

data "aws_secretsmanager_secret_version" "oauth_client_id" {
  count     = var.oauth_client_id_arn != null ? 1 : 0
  secret_id = data.aws_secretsmanager_secret.oauth_client_id[0].id
}

data "aws_secretsmanager_secret_version" "oauth_client_secret" {
  count     = var.oauth_client_secret_arn != null ? 1 : 0
  secret_id = data.aws_secretsmanager_secret.oauth_client_secret[0].id
}

locals {
  base_secrets = [
    {
      name      = "TRACECAT__DB_PASS"
      valueFrom = data.aws_secretsmanager_secret_version.tracecat_db_password.arn
    },
    {
      name      = "TRACECAT__SERVICE_KEY"
      valueFrom = data.aws_secretsmanager_secret_version.tracecat_service_key.arn
    },
    {
      name      = "TRACECAT__SIGNING_SECRET"
      valueFrom = data.aws_secretsmanager_secret_version.tracecat_signing_secret.arn
    },
    {
      name      = "TRACECAT__DB_ENCRYPTION_KEY"
      valueFrom = data.aws_secretsmanager_secret_version.tracecat_db_encryption_key.arn
    },
  ]

  oauth_client_id_secret = var.oauth_client_id_arn != null ? [
    {
      name      = "OAUTH_CLIENT_ID"
      valueFrom = data.aws_secretsmanager_secret_version.oauth_client_id[0].arn
    }
  ] : []

  oauth_client_secret_secret = var.oauth_client_secret_arn != null ? [
    {
      name      = "OAUTH_CLIENT_SECRET"
      valueFrom = data.aws_secretsmanager_secret_version.oauth_client_secret[0].arn
    }
  ] : []

  tracecat_secrets = concat(local.base_secrets, local.oauth_client_id_secret, local.oauth_client_secret_secret)
  temporal_secrets = [
    {
      name      = "POSTGRES_PWD"
      valueFrom = data.aws_secretsmanager_secret_version.temporal_db_password.arn
    }
  ]
}
