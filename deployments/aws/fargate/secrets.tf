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
  count = var.oauth_client_id != null ? 1 : 0
  arn   = var.oauth_client_id
}

data "aws_secretsmanager_secret" "oauth_client_secret" {
  count = var.oauth_client_secret != null ? 1 : 0
  arn   = var.oauth_client_secret
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
  count     = var.oauth_client_id != null ? 1 : 0
  secret_id = data.aws_secretsmanager_secret.oauth_client_id[0].id
}

data "aws_secretsmanager_secret_version" "oauth_client_secret" {
  count     = var.oauth_client_secret != null ? 1 : 0
  secret_id = data.aws_secretsmanager_secret.oauth_client_secret[0].id
}

# Local variable to store secrets
locals {
  secrets = {
    TRACECAT__DB_PASSWORD      = data.aws_secretsmanager_secret_version.tracecat_db_password.secret_string
    TEMPORAL__DB_PASSWORD      = data.aws_secretsmanager_secret_version.temporal_db_password.secret_string
    TRACECAT__DB_ENCRYPTION_KEY = data.aws_secretsmanager_secret_version.tracecat_db_encryption_key.secret_string
    TRACECAT__SERVICE_KEY      = data.aws_secretsmanager_secret_version.tracecat_service_key.secret_string
    TRACECAT__SIGNING_SECRET   = data.aws_secretsmanager_secret_version.tracecat_signing_secret.secret_string
    OAUTH_CLIENT_ID            = var.oauth_client_id != null ? data.aws_secretsmanager_secret_version.oauth_client_id[0].secret_string : null
    OAUTH_CLIENT_SECRET        = var.oauth_client_secret != null ? data.aws_secretsmanager_secret_version.oauth_client_secret[0].secret_string : null
  }
}
