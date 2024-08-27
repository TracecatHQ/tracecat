# Tracecat and Temporal Environment Variables
locals {

  public_app_url         = "https://${var.domain_name}"
  public_api_url         = "https://${var.domain_name}/api"
  internal_api_url       = "http://api-service:8000" # Service connect DNS name
  temporal_cluster_url   = "temporal-service:7233"
  temporal_cluster_queue = "tracecat-task-queue"
  allow_origins          = "${var.domain_name},http://ui-service:3000" # Allow api service and public app to access the API

  # Tracecat postgres env vars
  # See: https://github.com/TracecatHQ/tracecat/blob/abd5ff/tracecat/db/engine.py#L21
  tracecat_db_configs = {
    # NOTE: still missing
    # TRACECAT__DB_ENDPOINT which is the hostname of the RDS instance (from RDS resource)
    # TRACECAT__DB_PASS which is the password for the database (from secrets manager)
    TRACECAT__DB_USER = "postgres"
    TRACECAT__DB_PORT = "5432"
    TRACECAT__DB_NAME = "postgres" # Hardcoded in RDS resource configs
  }

  api_env = [
    for k, v in merge({
      LOG_LEVEL                   = var.log_level
      TRACECAT__API_URL           = local.internal_api_url
      TRACECAT__API_ROOT_PATH     = "/api"
      TRACECAT__APP_ENV           = var.tracecat_app_env
      TRACECAT__PUBLIC_RUNNER_URL = local.public_api_url
      TRACECAT__PUBLIC_APP_URL    = local.public_app_url
      TRACECAT__ALLOW_ORIGINS     = local.allow_origins
      TRACECAT__AUTH_TYPES        = var.auth_types
      TEMPORAL__CLUSTER_URL       = local.temporal_cluster_url
      TEMPORAL__CLUSTER_QUEUE     = local.temporal_cluster_queue
      RUN_MIGRATIONS              = "true"
    }, local.tracecat_db_configs) :
    { name = k, value = tostring(v) }
  ]

  worker_env = [
    for k, v in merge({
      LOG_LEVEL                   = var.log_level
      TRACECAT__API_URL           = local.internal_api_url
      TRACECAT__API_ROOT_PATH     = "/api"
      TRACECAT__APP_ENV           = var.tracecat_app_env
      TRACECAT__PUBLIC_RUNNER_URL = local.public_api_url
      TEMPORAL__CLUSTER_URL       = local.temporal_cluster_url
      TEMPORAL__CLUSTER_QUEUE     = local.temporal_cluster_queue
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
      LOG_LEVEL                  = "warn"
      TEMPORAL_BROADCAST_ADDRESS = "0.0.0.0"
      BIND_ON_IP                 = "0.0.0.0"
    } :
    { name = k, value = tostring(v) }
  ]
}
