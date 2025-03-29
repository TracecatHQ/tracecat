resource "aws_security_group" "alb" {

  # Name prefix: https://github.com/hashicorp/terraform/issues/3341
  name_prefix = "alb-"
  description = "Allow inbound HTTP/HTTPS access to the ALB"
  vpc_id      = var.vpc_id

  lifecycle {
    create_before_destroy = true
  }

  ingress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = var.allowed_inbound_cidr_blocks
  }

  ingress {
    protocol    = "tcp"
    from_port   = 80
    to_port     = 80
    cidr_blocks = var.allowed_inbound_cidr_blocks
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "caddy" {
  name_prefix = "caddy-"
  description = "Allow inbound access from the ALB to port 80 (Caddy)"
  vpc_id      = var.vpc_id

  lifecycle {
    create_before_destroy = true
  }

  ingress {
    description     = "Allow inbound access from ALB to port 80 (Caddy)"
    protocol        = "tcp"
    from_port       = 80
    to_port         = 80
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description = "Allow Caddy to forward traffic to API service"
    protocol    = "tcp"
    from_port   = 8000
    to_port     = 8000
    self        = true
  }

  ingress {
    description = "Allow Caddy to forward traffic to UI service"
    protocol    = "tcp"
    from_port   = 3000
    to_port     = 3000
    self        = true
  }

  ingress {
    description = "Allow Caddy to forward traffic to Temporal UI service"
    protocol    = "tcp"
    from_port   = 8080
    to_port     = 8080
    self        = true
  }

  ingress {
    description = "Allow Caddy to forward traffic to Metrics service"
    protocol    = "tcp"
    from_port   = 9000
    to_port     = 9000
    self        = true
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "core" {
  name_prefix = "core-"
  description = "Security group for core Tracecat services"
  vpc_id      = var.vpc_id

  lifecycle {
    create_before_destroy = true
  }

  ingress {
    description = "Allow internal traffic to the Tracecat API service on port 8000"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    self        = true
  }

  ingress {
    description = "Allow internal traffic to the Tracecat Worker service on port 8001"
    from_port   = 8001
    to_port     = 8001
    protocol    = "tcp"
    self        = true
  }

  ingress {
    description = "Allow internal traffic to the Tracecat Executor service on port 8000"
    from_port   = 8002
    to_port     = 8002
    protocol    = "tcp"
    self        = true
  }

  ingress {
    description = "Allow internal traffic to the Tracecat UI service on port 3000"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    self        = true
  }

  ingress {
    description = "Allow internal traffic to the Temporal server on port 7233"
    from_port   = 7233
    to_port     = 7233
    protocol    = "tcp"
    self        = true
  }

  ingress {
    description = "Allow inbound traffic for metrics service"
    from_port   = 9000
    to_port     = 9000
    protocol    = "tcp"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

}

resource "aws_security_group" "core_db" {
  name_prefix = "core-db-"
  description = "Security group for Tracecat API to RDS communication"
  vpc_id      = var.vpc_id

  lifecycle {
    create_before_destroy = true
  }

  ingress {
    description     = "Allow inbound traffic to PostgreSQL database on port 5432"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.core.id]
  }

  egress {
    from_port       = 0
    to_port         = 0
    protocol        = "-1"
    security_groups = [aws_security_group.core.id]
  }

}

resource "aws_security_group" "temporal" {
  name_prefix = "temporal-"
  description = "Security group for Temporal server"
  vpc_id      = var.vpc_id

  lifecycle {
    create_before_destroy = true
  }

  ingress {
    description = "Allow inbound traffic to Temporal server on port 7233"
    from_port   = 7233
    to_port     = 7233
    protocol    = "tcp"
    self        = true
  }

  ingress {
    description = "Allow inbound traffic for port forwarding to Temporal UI"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    self        = true
  }
}

resource "aws_security_group" "temporal_db" {
  name_prefix = "temporal-db-"
  description = "Security group for Temporal server to RDS communication"
  vpc_id      = var.vpc_id

  lifecycle {
    create_before_destroy = true
  }

  ingress {
    description     = "Allow inbound traffic to PostgreSQL database on port 5432"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.core.id]
  }

  egress {
    from_port       = 0
    to_port         = 0
    protocol        = "-1"
    security_groups = [aws_security_group.core.id]
  }

}

resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.secretsmanager_vpc_endpoint.id]
  private_dns_enabled = true
}

resource "aws_security_group" "secretsmanager_vpc_endpoint" {
  name_prefix = "secretsmanager-vpc-endpoint-"
  description = "Security group for Secrets Manager VPC endpoint"
  vpc_id      = var.vpc_id

  lifecycle {
    create_before_destroy = true
  }

  ingress {
    from_port = 443
    to_port   = 443
    protocol  = "tcp"
    security_groups = [
      aws_security_group.core.id,
      aws_security_group.temporal.id
    ]
  }

  egress {
    from_port = 0
    to_port   = 0
    protocol  = "-1"
    security_groups = [
      aws_security_group.core.id,
      aws_security_group.temporal.id
    ]
  }
}
