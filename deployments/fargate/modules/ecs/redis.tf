# subnet group (or use an existing one from your network module)
resource "aws_elasticache_subnet_group" "redis" {
  name       = "tracecat-redis-subnet"
  subnet_ids = var.private_subnet_ids
}

resource "random_password" "redis_app_user_password" {
  length  = 40
  special = false
}

# Default user (required by AWS ElastiCache)
resource "aws_elasticache_user" "default" {
  user_id       = "default-user-tracecat"
  user_name     = "default" # Must be named "default"
  engine        = "redis"
  access_string = "off ~* -@all" # Disabled user with no access
  authentication_mode {
    type = "no-password-required"
  }

  lifecycle {
    # Provider bug: AWS returns "no-password" so Terraform thinks auth changed each plan.
    ignore_changes = [authentication_mode]
  }
}

# Application user
resource "aws_elasticache_user" "app_user" {
  user_id       = "tracecat-app"
  user_name     = "tracecat-app" # App connections will use this username
  engine        = "redis"
  access_string = "on ~* +@all" # Full access; refine later if needed

  # Require password auth so Redis access is not SG-only.
  authentication_mode {
    type      = "password"
    passwords = [random_password.redis_app_user_password.result]
  }
}

resource "aws_elasticache_user_group" "redis" {
  user_group_id = "tracecat-users"
  engine        = "redis"
  user_ids = [
    aws_elasticache_user.default.user_id,
    aws_elasticache_user.app_user.user_id
  ]

  lifecycle {
    create_before_destroy = true
  }
}

# The replication group (single-node, TLS & KMS encryption are ON by default in Redis 7)
resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "tracecat-redis"
  description          = "Tracecat Redis - password-protected app user"
  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.redis_node_type # default cache.t3.micro
  num_cache_clusters   = 1                   # single AZ, no failover
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  user_group_ids = [aws_elasticache_user_group.redis.id]

  transit_encryption_enabled = true # always enable TLS
  at_rest_encryption_enabled = true
}

# Store the complete Redis URL in Secrets Manager so ECS tasks can inject it.
resource "aws_secretsmanager_secret" "redis_url" {
  name_prefix = "tracecat-redis-url-"
  description = "Tracecat Redis connection URL"
}

resource "aws_secretsmanager_secret_version" "redis_url" {
  secret_id = aws_secretsmanager_secret.redis_url.id
  secret_string = format(
    "rediss://%s:%s@%s:%d",
    aws_elasticache_user.app_user.user_name,
    random_password.redis_app_user_password.result,
    aws_elasticache_replication_group.redis.primary_endpoint_address,
    aws_elasticache_replication_group.redis.port,
  )
}
