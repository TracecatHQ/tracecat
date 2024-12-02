resource "aws_security_group" "alb" {
  name        = "alb-security-group"
  description = "Allow inbound HTTP/HTTPS access to the ALB"
  vpc_id      = var.vpc_id

  ingress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    protocol    = "tcp"
    from_port   = 80
    to_port     = 80
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
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
    cidr_blocks = ["0.0.0.0/0"]
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
    description = "Allow inbound traffic to PostgreSQL database on port 5432"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

}

resource "aws_security_group" "temporal_db" {
  name        = "temporal-db-security-group"
  description = "Security group for Tracecat API to RDS communication"
  vpc_id      = var.vpc_id

  ingress {
    description = "Allow inbound traffic to PostgreSQL database on port 5432"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

}
