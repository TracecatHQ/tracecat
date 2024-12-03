data "aws_vpc" "this" {
  id = var.vpc_id
}

resource "aws_security_group" "alb" {
  name        = "alb-security-group"
  description = "Allow inbound HTTP/HTTPS access to the ALB"
  vpc_id      = var.vpc_id

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
    cidr_blocks = coalesce(var.allowed_outbound_cidr_blocks, [data.aws_vpc.this.cidr_block])
  }
}

resource "aws_security_group" "caddy" {
  name        = "caddy-security-group"
  description = "Allow inbound access from the ALB to port 80 (Caddy) only"
  vpc_id      = var.vpc_id

  ingress {
    protocol        = "tcp"
    from_port       = 80
    to_port         = 80
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = [data.aws_vpc.this.cidr_block]
  }
}

resource "aws_security_group" "core" {
  name        = "core-security-group"
  description = "Security group for core Tracecat services"
  vpc_id      = var.vpc_id

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

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

}

resource "aws_security_group" "core_db" {
  name        = "core-db-security-group"
  description = "Security group for Tracecat API to RDS communication"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Allow inbound traffic to PostgreSQL database on port 5432"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.core.id]
  }

  egress {
    from_port = 0
    to_port   = 0
    protocol  = "-1"
    # Need to reach GitHub image registry
    cidr_blocks     = ["0.0.0.0/0"]
    security_groups = [aws_security_group.core.id]
  }

}

resource "aws_security_group" "temporal_db" {
  name        = "temporal-db-security-group"
  description = "Security group for Temporal server to RDS communication"
  vpc_id      = var.vpc_id

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
    cidr_blocks     = ["0.0.0.0/0"]
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
  name        = "secretsmanager-vpc-endpoint"
  description = "Security group for Secrets Manager VPC endpoint"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.core.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
