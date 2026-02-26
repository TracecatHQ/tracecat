# Tracecat and Temporal environment variables
locals {

  # Tracecat version
  tracecat_image_tag = var.tracecat_image_tag

  # Tracecat common URLs
  public_app_url   = "https://${var.domain_name}"
  public_api_url   = "https://${var.domain_name}/api"
  internal_api_url = "http://api-service:8000" # Service connect DNS name

  temporal_cluster_url   = var.temporal_cluster_url
  temporal_cluster_queue = var.temporal_cluster_queue
  temporal_namespace     = var.temporal_namespace
  allow_origins          = "https://${var.domain_name},http://ui-service:3000"

  # Tracecat Postgres env vars
  tracecat_db_configs = {
    TRACECAT__DB_USER         = "postgres"
    TRACECAT__DB_PORT         = "5432"
    TRACECAT__DB_NAME         = "postgres" # Hardcoded in RDS resource configs
    TRACECAT__DB_PASS__ARN    = data.aws_secretsmanager_secret_version.tracecat_db_password.arn
    TRACECAT__DB_MAX_OVERFLOW = var.db_max_overflow
    TRACECAT__DB_POOL_SIZE    = var.db_pool_size
    TRACECAT__DB_POOL_TIMEOUT = var.db_pool_timeout
    TRACECAT__DB_POOL_RECYCLE = var.db_pool_recycle
  }

  tracecat_db_configs_executor = {
    TRACECAT__DB_MAX_OVERFLOW = var.db_max_overflow_executor
    TRACECAT__DB_POOL_SIZE    = var.db_pool_size_executor
  }

  tracecat_common_env = {
    LOG_LEVEL                                        = var.log_level
    TEMPORAL__CLUSTER_NAMESPACE                      = local.temporal_namespace
    TEMPORAL__CLUSTER_URL                            = local.temporal_cluster_url
    TRACECAT__APP_ENV                                = var.tracecat_app_env
    TRACECAT__FEATURE_FLAGS                          = var.feature_flags # Requires Tracecat Enterprise license to modify.
    TRACECAT__EE_MULTI_TENANT                        = var.ee_multi_tenant
    TRACECAT__CONTEXT_COMPRESSION_ENABLED            = var.context_compression_enabled
    TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB       = var.context_compression_threshold_kb
    TRACECAT__RESULT_EXTERNALIZATION_ENABLED         = var.result_externalization_enabled
    TRACECAT__COLLECTION_MANIFESTS_ENABLED           = var.collection_manifests_enabled
    TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES = var.result_externalization_threshold_bytes
    TRACECAT__REGISTRY_SYNC_BUILTIN_USE_INSTALLED_SITE_PACKAGES = var.registry_sync_builtin_use_installed_site_packages
    TRACECAT__DB_SSLMODE                             = "require"
  }

  tracecat_blob_storage_env = {
    TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS = aws_s3_bucket.attachments.bucket
    TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY    = aws_s3_bucket.registry.bucket
    TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW    = aws_s3_bucket.workflow.bucket
  }

  api_env = [
    for k, v in merge(
      local.tracecat_common_env,
      local.tracecat_blob_storage_env,
      local.tracecat_db_configs,
      {
        TRACECAT__ALLOW_ORIGINS                    = local.allow_origins
        TRACECAT__API_ROOT_PATH                    = "/api"
        TRACECAT__API_URL                          = local.internal_api_url
        TRACECAT__PUBLIC_API_URL                   = local.public_api_url
        TRACECAT__PUBLIC_APP_URL                   = local.public_app_url
        TRACECAT__AUTH_TYPES                       = var.auth_types
        TRACECAT__AUTH_ALLOWED_DOMAINS             = var.auth_allowed_domains
        TRACECAT__AUTH_MIN_PASSWORD_LENGTH         = var.auth_min_password_length
        TRACECAT__AUTH_SUPERADMIN_EMAIL            = var.auth_superadmin_email
        TRACECAT__DB_ENDPOINT                      = local.core_db_hostname
        OIDC_ISSUER                                = var.oidc_issuer
        OIDC_SCOPES                                = var.oidc_scopes
        TEMPORAL__CLUSTER_QUEUE                    = local.temporal_cluster_queue
        SAML_ALLOW_UNSOLICITED                     = var.saml_allow_unsolicited
        SAML_AUTHN_REQUESTS_SIGNED                 = var.saml_authn_requests_signed
        SAML_SIGNED_ASSERTIONS                     = var.saml_signed_assertions
        SAML_SIGNED_RESPONSES                      = var.saml_signed_responses
        SAML_VERIFY_SSL_ENTITY                     = var.saml_verify_ssl_entity
        SAML_VERIFY_SSL_METADATA                   = var.saml_verify_ssl_metadata
        TRACECAT__WORKFLOW_ARTIFACT_RETENTION_DAYS = var.workflow_artifact_retention_days
      }
    ) :
    { name = k, value = tostring(v) } if v != null
  ]

  worker_env = [
    for k, v in merge(
      local.tracecat_common_env,
      local.tracecat_blob_storage_env,
      local.tracecat_db_configs,
      {
        TRACECAT__API_ROOT_PATH           = "/api"
        TRACECAT__API_URL                 = local.internal_api_url
        TRACECAT__DB_ENDPOINT             = local.core_db_hostname
        TRACECAT__PUBLIC_API_URL          = local.public_api_url
        TRACECAT__EXECUTOR_CLIENT_TIMEOUT = var.executor_client_timeout
        TEMPORAL__CLUSTER_QUEUE           = local.temporal_cluster_queue
        SENTRY_DSN                        = var.sentry_dsn
      }
    ) :
    { name = k, value = tostring(v) } if v != null
  ]

  executor_env = [
    for k, v in merge(
      local.tracecat_common_env,
      local.tracecat_blob_storage_env,
      local.tracecat_db_configs,
      local.tracecat_db_configs_executor,
      {
        TRACECAT__API_URL                   = local.internal_api_url
        TRACECAT__DB_ENDPOINT               = local.core_db_hostname
        TRACECAT__EXECUTOR_BACKEND          = "direct"
        TRACECAT__EXECUTOR_QUEUE            = var.executor_queue
        TRACECAT__EXECUTOR_WORKER_POOL_SIZE = var.executor_worker_pool_size
        TRACECAT__UNSAFE_DISABLE_SM_MASKING = "false"
        TRACECAT__DISABLE_NSJAIL            = "true"
        TRACECAT__SANDBOX_NSJAIL_PATH       = "/usr/local/bin/nsjail"
        TRACECAT__SANDBOX_ROOTFS_PATH       = "/var/lib/tracecat/sandbox-rootfs"
        TRACECAT__SANDBOX_CACHE_DIR         = "/var/lib/tracecat/sandbox-cache"
      }
    ) :
    { name = k, value = tostring(v) } if v != null
  ]

  agent_executor_env = [
    for k, v in merge(
      local.tracecat_common_env,
      local.tracecat_blob_storage_env,
      local.tracecat_db_configs,
      local.tracecat_db_configs_executor,
      {
        TRACECAT__API_URL                   = local.internal_api_url
        TRACECAT__DB_ENDPOINT               = local.core_db_hostname
        TRACECAT__EXECUTOR_BACKEND          = "direct"
        TRACECAT__AGENT_QUEUE               = var.agent_queue
        TRACECAT__EXECUTOR_WORKER_POOL_SIZE = var.agent_executor_worker_pool_size
        TRACECAT__UNSAFE_DISABLE_SM_MASKING = "false"
        TRACECAT__DISABLE_NSJAIL            = "true"
        TRACECAT__SANDBOX_NSJAIL_PATH       = "/usr/local/bin/nsjail"
        TRACECAT__SANDBOX_ROOTFS_PATH       = "/var/lib/tracecat/sandbox-rootfs"
        TRACECAT__SANDBOX_CACHE_DIR         = "/var/lib/tracecat/sandbox-cache"
      }
    ) :
    { name = k, value = tostring(v) } if v != null
  ]

  migrations_env = [
    for k, v in merge(
      {
        LOG_LEVEL                  = var.log_level
        TRACECAT__DB_SSLMODE       = "require"
        TRACECAT__DB_ENDPOINT      = local.core_db_hostname
        TRACECAT__FEATURE_FLAGS    = var.feature_flags
      },
      local.tracecat_db_configs
    ) :
    { name = k, value = tostring(v) } if v != null
  ]

  ui_env = [
    for k, v in {
      NEXT_PUBLIC_API_URL    = local.public_api_url
      NEXT_PUBLIC_APP_ENV    = var.tracecat_app_env
      NEXT_PUBLIC_APP_URL    = local.public_app_url
      NEXT_PUBLIC_AUTH_TYPES = var.auth_types
      NEXT_SERVER_API_URL    = local.internal_api_url
      NODE_ENV               = "production"
    } :
    { name = k, value = tostring(v) } if v != null
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
