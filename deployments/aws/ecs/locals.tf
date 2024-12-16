# Tracecat and Temporal Environment Variables
locals {

  # Tracecat version
  git_sha            = var.TFC_CONFIGURATION_VERSION_GIT_COMMIT_SHA
  sha256_image_tag   = local.git_sha != null ? "sha-${substr(local.git_sha, 0, 7)}" : null
  tracecat_image_tag = var.use_git_commit_sha ? local.sha256_image_tag : var.tracecat_image_tag

  # Tracecat common URLs
  public_app_url         = "https://${var.domain_name}"
  public_api_url         = "https://${var.domain_name}/api"
  saml_acs_url           = "https://${var.domain_name}/api/auth/saml/acs"
  internal_api_url       = "http://api-service:8000"      # Service connect DNS name
  internal_executor_url  = "http://executor-service:8002" # Service connect DNS name
  temporal_cluster_url   = "temporal-service:7233"
  temporal_cluster_queue = "tracecat-task-queue"
  allow_origins          = "${var.domain_name},http://ui-service:3000" # Allow api service and public app to access the API

  # Tracecat postgres env vars
  # See: https://github.com/TracecatHQ/tracecat/blob/abd5ff/tracecat/db/engine.py#L21
  tracecat_db_configs = {
    TRACECAT__DB_USER      = "postgres"
    TRACECAT__DB_PORT      = "5432"
    TRACECAT__DB_NAME      = "postgres" # Hardcoded in RDS resource configs
    TRACECAT__DB_PASS__ARN = data.aws_secretsmanager_secret_version.tracecat_db_password.arn
  }

  api_env = [
    for k, v in merge({
      LOG_LEVEL                                = var.log_level
      RUN_MIGRATIONS                           = "true"
      SAML_SP_ACS_URL                          = local.saml_acs_url
      TEMPORAL__CLIENT_RPC_TIMEOUT             = var.temporal_client_rpc_timeout
      TEMPORAL__CLUSTER_QUEUE                  = local.temporal_cluster_queue
      TEMPORAL__CLUSTER_URL                    = local.temporal_cluster_url
      TRACECAT__ALLOW_ORIGINS                  = local.allow_origins
      TRACECAT__API_ROOT_PATH                  = "/api"
      TRACECAT__API_URL                        = local.internal_api_url
      TRACECAT__APP_ENV                        = var.tracecat_app_env
      TRACECAT__AUTH_ALLOWED_DOMAINS           = var.auth_allowed_domains
      TRACECAT__AUTH_TYPES                     = var.auth_types
      TRACECAT__DB_ENDPOINT                    = local.core_db_hostname
      TRACECAT__PUBLIC_APP_URL                 = local.public_app_url
      TRACECAT__PUBLIC_API_URL                 = local.public_api_url
      TRACECAT__REMOTE_REPOSITORY_PACKAGE_NAME = var.remote_repository_package_name
      TRACECAT__REMOTE_REPOSITORY_URL          = var.remote_repository_url
      TRACECAT__EXECUTOR_URL                   = local.internal_executor_url
    }, local.tracecat_db_configs) :
    { name = k, value = tostring(v) }
  ]

  worker_env = [
    for k, v in merge({
      LOG_LEVEL                    = var.log_level
      TRACECAT__API_URL            = local.internal_api_url
      TRACECAT__API_ROOT_PATH      = "/api"
      TRACECAT__APP_ENV            = var.tracecat_app_env
      TRACECAT__DB_ENDPOINT        = local.core_db_hostname
      TRACECAT__PUBLIC_API_URL     = local.public_api_url
      TEMPORAL__CLUSTER_URL        = local.temporal_cluster_url
      TEMPORAL__CLUSTER_QUEUE      = local.temporal_cluster_queue
      TEMPORAL__CLIENT_RPC_TIMEOUT = var.temporal_client_rpc_timeout
      TRACECAT__EXECUTOR_URL       = local.internal_executor_url
    }, local.tracecat_db_configs) :
    { name = k, value = tostring(v) }
  ]

  executor_env = [
    for k, v in merge({
      LOG_LEVEL                                = var.log_level
      TRACECAT__APP_ENV                        = var.tracecat_app_env
      TRACECAT__DB_ENDPOINT                    = local.core_db_hostname
      TRACECAT__REMOTE_REPOSITORY_URL          = var.remote_repository_url
      TRACECAT__REMOTE_REPOSITORY_PACKAGE_NAME = var.remote_repository_package_name
    }, local.tracecat_db_configs) :
    { name = k, value = tostring(v) }
  ]

  ui_env = [
    for k, v in {
      NEXT_PUBLIC_API_URL    = local.public_api_url
      NEXT_PUBLIC_APP_ENV    = var.tracecat_app_env
      NEXT_PUBLIC_APP_URL    = local.public_app_url
      NEXT_PUBLIC_AUTH_TYPES = var.auth_types
      NEXT_SERVER_API_URL    = local.internal_api_url
      NODE_ENV               = var.tracecat_app_env
    } :
    { name = k, value = tostring(v) }
  ]

  temporal_env = [
    for k, v in {
      DB                         = "postgres12"
      DB_PORT                    = "5432"
      POSTGRES_USER              = "postgres"
      LOG_LEVEL                  = var.temporal_log_level
      TEMPORAL_BROADCAST_ADDRESS = "0.0.0.0"
      BIND_ON_IP                 = "0.0.0.0"
      NUM_HISTORY_SHARDS         = var.temporal_num_history_shards
    } :
    { name = k, value = tostring(v) }
  ]
}
