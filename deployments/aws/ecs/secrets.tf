# Required secrets in AWS Secrets Manager:
# 1. TRACECAT__DB_ENCRYPTION_KEY
# 2. TRACECAT__SERVICE_KEY
# 3. TRACECAT__SIGNING_SECRET
#
# Optional secrets:
# 1. OAUTH_CLIENT_ID
# 2. OAUTH_CLIENT_SECRET

# Required secrets
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

data "aws_secretsmanager_secret" "saml_idp_entity_id" {
  count = var.saml_idp_entity_id_arn != null ? 1 : 0
  arn   = var.saml_idp_entity_id_arn
}

data "aws_secretsmanager_secret" "saml_idp_redirect_url" {
  count = var.saml_idp_redirect_url_arn != null ? 1 : 0
  arn   = var.saml_idp_redirect_url_arn
}

data "aws_secretsmanager_secret" "saml_idp_certificate" {
  count = var.saml_idp_certificate_arn != null ? 1 : 0
  arn   = var.saml_idp_certificate_arn
}

data "aws_secretsmanager_secret" "saml_idp_metadata_url" {
  count = var.saml_idp_metadata_url_arn != null ? 1 : 0
  arn   = var.saml_idp_metadata_url_arn
}

# Retrieve secret values

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

# SAML SSO secrets

data "aws_secretsmanager_secret_version" "saml_idp_entity_id" {
  count     = var.saml_idp_entity_id_arn != null ? 1 : 0
  secret_id = data.aws_secretsmanager_secret.saml_idp_entity_id[0].id
}

data "aws_secretsmanager_secret_version" "saml_idp_redirect_url" {
  count     = var.saml_idp_redirect_url_arn != null ? 1 : 0
  secret_id = data.aws_secretsmanager_secret.saml_idp_redirect_url[0].id
}

data "aws_secretsmanager_secret_version" "saml_idp_certificate" {
  count     = var.saml_idp_certificate_arn != null ? 1 : 0
  secret_id = data.aws_secretsmanager_secret.saml_idp_certificate[0].id
}

data "aws_secretsmanager_secret_version" "saml_idp_metadata_url" {
  count     = var.saml_idp_metadata_url_arn != null ? 1 : 0
  secret_id = data.aws_secretsmanager_secret.saml_idp_metadata_url[0].id
}

# Database secrets

data "aws_secretsmanager_secret" "tracecat_db_password" {
  arn        = aws_db_instance.core_database.master_user_secret[0].secret_arn
  depends_on = [aws_db_instance.core_database]
}

data "aws_secretsmanager_secret" "temporal_db_password" {
  arn        = aws_db_instance.temporal_database.master_user_secret[0].secret_arn
  depends_on = [aws_db_instance.temporal_database]
}

data "aws_secretsmanager_secret_version" "tracecat_db_password" {
  secret_id = data.aws_secretsmanager_secret.tracecat_db_password.id
}

data "aws_secretsmanager_secret_version" "temporal_db_password" {
  secret_id = data.aws_secretsmanager_secret.temporal_db_password.id
}

locals {
  tracecat_base_secrets = [
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

  saml_idp_entity_id_secret = var.saml_idp_entity_id_arn != null ? [
    {
      name      = "SAML_IDP_ENTITY_ID"
      valueFrom = data.aws_secretsmanager_secret_version.saml_idp_entity_id[0].arn
    }
  ] : []

  saml_idp_redirect_url_secret = var.saml_idp_redirect_url_arn != null ? [
    {
      name      = "SAML_IDP_REDIRECT_URL"
      valueFrom = data.aws_secretsmanager_secret_version.saml_idp_redirect_url[0].arn
    }
  ] : []

  saml_idp_certificate_secret = var.saml_idp_certificate_arn != null ? [
    {
      name      = "SAML_IDP_CERTIFICATE"
      valueFrom = data.aws_secretsmanager_secret_version.saml_idp_certificate[0].arn
    }
  ] : []

  saml_idp_metadata_url_secret = var.saml_idp_metadata_url_arn != null ? [
    {
      name      = "SAML_IDP_METADATA_URL"
      valueFrom = data.aws_secretsmanager_secret_version.saml_idp_metadata_url[0].arn
    }
  ] : []

  tracecat_api_secrets = concat(
    local.tracecat_base_secrets,
    local.oauth_client_id_secret,
    local.oauth_client_secret_secret,
    local.saml_idp_entity_id_secret,
    local.saml_idp_redirect_url_secret,
    local.saml_idp_certificate_secret,
    local.saml_idp_metadata_url_secret
  )

  temporal_secrets = [
    {
      name      = "POSTGRES_PWD"
      valueFrom = "${data.aws_secretsmanager_secret_version.temporal_db_password.arn}:password::"
    }
  ]
}
