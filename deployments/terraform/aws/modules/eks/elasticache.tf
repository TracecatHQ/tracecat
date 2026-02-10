# ElastiCache Security Group
resource "aws_security_group" "elasticache" {
  name        = "${var.cluster_name}-elasticache-sg"
  description = "Security group for ElastiCache Redis"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.tracecat_redis_client.id]
    description     = "Redis from Tracecat pods with SecurityGroupPolicy"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-elasticache-sg"
  })
}

# Pod security group for Redis access (used by SecurityGroupPolicy)
resource "aws_security_group" "tracecat_redis_client" {
  name        = "${var.cluster_name}-redis-client-sg"
  description = "Pod security group for Tracecat Redis access"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [data.aws_vpc.selected.cidr_block]
    description = "Allow intra-VPC traffic to Tracecat pods"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-redis-client-sg"
  })
}

# ElastiCache Subnet Group
resource "aws_elasticache_subnet_group" "tracecat" {
  name       = "${var.cluster_name}-elasticache-subnet-group"
  subnet_ids = var.private_subnet_ids

  tags = var.tags
}

# Redis auth token (ElastiCache constraint: 16-128 printable ASCII, no special chars like @ or /)
resource "random_password" "redis_auth" {
  length  = 32
  special = false
}

# ElastiCache Redis Replication Group
resource "aws_elasticache_replication_group" "tracecat" {
  replication_group_id = "${var.cluster_name}-redis"
  description          = "Redis cluster for Tracecat"

  engine             = "redis"
  engine_version     = "7.1"
  node_type          = var.elasticache_node_type
  num_cache_clusters = 1
  port               = 6379

  subnet_group_name  = aws_elasticache_subnet_group.tracecat.name
  security_group_ids = [aws_security_group.elasticache.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis_auth.result

  automatic_failover_enabled = false
  multi_az_enabled           = false

  snapshot_retention_limit = 1
  snapshot_window          = "03:00-04:00"
  maintenance_window       = "Mon:04:00-Mon:05:00"

  apply_immediately = true

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-redis"
  })
}
