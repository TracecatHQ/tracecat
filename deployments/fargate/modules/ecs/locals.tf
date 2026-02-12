# Tracecat and Temporal Environment Variables
locals {

  # Tracecat version
  tracecat_image_tag = var.tracecat_image_tag

  # Tracecat common URLs
  public_app_url         = "https://${var.domain_name}"
  public_api_url         = "https://${var.domain_name}/api"
  internal_api_url       = "http://api-service:8000"      # Service connect DNS name
  internal_executor_url  = "http://executor-service:8002" # Service connect DNS name
  temporal_cluster_url   = var.temporal_cluster_url
  temporal_cluster_queue = var.temporal_cluster_queue
  temporal_namespace     = var.temporal_namespace
  allow_origins          = "${var.domain_name},http://ui-service:3000" # Allow api service and public app to access the API

  # Redis configuration with IAM auth
  redis_host = aws_elasticache_replication_group.redis.primary_endpoint_address
  redis_port = tostring(aws_elasticache_replication_group.redis.port)
  redis_url  = "rediss://${aws_elasticache_user.app_user.user_name}@${aws_elasticache_replication_group.redis.primary_endpoint_address}:${aws_elasticache_replication_group.redis.port}"

  # Temporal client authentication
  temporal_api_key_arn = var.temporal_api_key_arn

  # Tracecat postgres env vars
  # See: https://github.com/TracecatHQ/tracecat/blob/abd5ff/tracecat/db/engine.py#L21
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

  api_env = [
    for k, v in merge({
      LOG_LEVEL                                  = var.log_level
      RUN_MIGRATIONS                             = "true"
      TEMPORAL__CLIENT_RPC_TIMEOUT               = var.temporal_client_rpc_timeout
      TEMPORAL__TASK_TIMEOUT                     = var.temporal_task_timeout
      TEMPORAL__CLUSTER_NAMESPACE                = local.temporal_namespace
      TEMPORAL__CLUSTER_QUEUE                    = local.temporal_cluster_queue
      TEMPORAL__CLUSTER_URL                      = local.temporal_cluster_url
      TEMPORAL__API_KEY__ARN                     = local.temporal_api_key_arn
      TRACECAT__ALLOW_ORIGINS                    = local.allow_origins
      TRACECAT__API_ROOT_PATH                    = "/api"
      TRACECAT__API_URL                          = local.internal_api_url
      TRACECAT__APP_ENV                          = var.tracecat_app_env
      TRACECAT__AUTH_ALLOWED_DOMAINS             = var.auth_allowed_domains
      TRACECAT__AUTH_SUPERADMIN_EMAIL            = var.auth_superadmin_email
      TRACECAT__AUTH_TYPES                       = var.auth_types
      TRACECAT__DB_ENDPOINT                      = local.core_db_hostname
      TRACECAT__EXECUTOR_BACKEND                 = "direct"
      TRACECAT__EXECUTOR_URL                     = local.internal_executor_url
      TRACECAT__PUBLIC_API_URL                   = local.public_api_url
      TRACECAT__PUBLIC_APP_URL                   = local.public_app_url
      TRACECAT__UNIFIED_AGENT_STREAMING_ENABLED  = "false"
      TRACECAT__CONTEXT_COMPRESSION_ENABLED      = var.context_compression_enabled
      TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB = var.context_compression_threshold_kb
      TRACECAT__BLOB_STORAGE_PROTOCOL            = "s3"
      TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS  = aws_s3_bucket.attachments.bucket
      TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY     = var.use_legacy_executor ? null : aws_s3_bucket.registry[0].bucket
      TRACECAT__FEATURE_FLAGS                    = var.feature_flags # Requires Tracecat Enterprise license to modify.
      # Redis
      REDIS_HOST = local.redis_host
      REDIS_PORT = local.redis_port
      REDIS_URL  = local.redis_url
    }, local.tracecat_db_configs) :
    { name = k, value = tostring(v) } if v != null
  ]

  worker_env = [
    for k, v in merge({
      LOG_LEVEL                                  = var.log_level
      TEMPORAL__CLIENT_RPC_TIMEOUT               = var.temporal_client_rpc_timeout
      TEMPORAL__TASK_TIMEOUT                     = var.temporal_task_timeout
      TEMPORAL__CLUSTER_NAMESPACE                = local.temporal_namespace
      TEMPORAL__CLUSTER_QUEUE                    = local.temporal_cluster_queue
      TEMPORAL__CLUSTER_URL                      = local.temporal_cluster_url
      TEMPORAL__API_KEY__ARN                     = local.temporal_api_key_arn
      TRACECAT__API_ROOT_PATH                    = "/api"
      TRACECAT__API_URL                          = local.internal_api_url
      TRACECAT__APP_ENV                          = var.tracecat_app_env
      TRACECAT__DB_ENDPOINT                      = local.core_db_hostname
      TRACECAT__EXECUTOR_BACKEND                 = "direct"
      TRACECAT__EXECUTOR_CLIENT_TIMEOUT          = var.executor_client_timeout
      TRACECAT__EXECUTOR_URL                     = local.internal_executor_url
      TRACECAT__PUBLIC_API_URL                   = local.public_api_url
      TEMPORAL__METRICS_PORT                     = var.enable_metrics ? 9000 : null
      SENTRY_DSN                                 = var.sentry_dsn
      TRACECAT__CONTEXT_COMPRESSION_ENABLED      = var.context_compression_enabled
      TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB = var.context_compression_threshold_kb
      TRACECAT__FEATURE_FLAGS                    = var.feature_flags # Requires Tracecat Enterprise license to modify.
      # Redis
      REDIS_HOST = local.redis_host
      REDIS_PORT = local.redis_port
      REDIS_URL  = local.redis_url
    }, local.tracecat_db_configs) :
    { name = k, value = tostring(v) }
  ]

  executor_env = [
    for k, v in merge({
      LOG_LEVEL                                  = var.log_level
      TEMPORAL__CLIENT_RPC_TIMEOUT               = var.temporal_client_rpc_timeout
      TEMPORAL__TASK_TIMEOUT                     = var.temporal_task_timeout
      TEMPORAL__CLUSTER_NAMESPACE                = local.temporal_namespace
      TEMPORAL__CLUSTER_QUEUE                    = local.temporal_cluster_queue
      TEMPORAL__CLUSTER_URL                      = local.temporal_cluster_url
      TEMPORAL__API_KEY__ARN                     = local.temporal_api_key_arn
      TRACECAT__API_URL                          = local.internal_api_url
      TRACECAT__APP_ENV                          = var.tracecat_app_env
      TRACECAT__DB_ENDPOINT                      = local.core_db_hostname
      TRACECAT__CONTEXT_COMPRESSION_ENABLED      = var.context_compression_enabled
      TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB = var.context_compression_threshold_kb
      TRACECAT__EXECUTOR_PAYLOAD_MAX_SIZE_BYTES  = var.executor_payload_max_size_bytes
      TRACECAT__EXECUTOR_QUEUE                   = "shared-action-queue"
      TRACECAT__DISABLE_NSJAIL                   = "true"
      TRACECAT__BLOB_STORAGE_PROTOCOL            = "s3"
      TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS  = aws_s3_bucket.attachments.bucket
      TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY     = var.use_legacy_executor ? null : aws_s3_bucket.registry[0].bucket
      TRACECAT__FEATURE_FLAGS                    = var.feature_flags # Requires Tracecat Enterprise license to modify.
      RAY_RUNTIME_ENV_UV_CACHE_SIZE_GB           = var.executor_ray_runtime_env_uv_cache_size_gb
      # Redis
      REDIS_HOST = local.redis_host
      REDIS_PORT = local.redis_port
      REDIS_URL  = local.redis_url
    }, local.tracecat_db_configs, local.tracecat_db_configs_executor) :
    { name = k, value = tostring(v) } if v != null
  ]

  ui_env = [
    for k, v in {
      NEXT_PUBLIC_API_URL     = local.public_api_url
      NEXT_PUBLIC_APP_ENV     = var.tracecat_app_env
      NEXT_PUBLIC_APP_URL     = local.public_app_url
      NEXT_PUBLIC_AUTH_TYPES  = var.auth_types
      NEXT_SERVER_API_URL     = local.internal_api_url
      NODE_ENV                = var.tracecat_app_env
      TRACECAT__FEATURE_FLAGS = var.feature_flags # Requires Tracecat Enterprise license to modify.
      # Redis
      REDIS_HOST = local.redis_host
      REDIS_PORT = local.redis_port
      REDIS_URL  = local.redis_url
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
