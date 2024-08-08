resource "aws_security_group" "ui_lb" {
    name        = "ui-load-balancer-security-group"
    description = "controls access to the ALB"
    vpc_id      = aws_vpc.tracecat.id

    ingress {
        protocol    = "tcp"
        from_port   = "443" 
        to_port     = "443" 
        cidr_blocks = ["0.0.0.0/0"]
    }

    egress {
        protocol    = "-1"
        from_port   = 0
        to_port     = 0
        cidr_blocks = ["0.0.0.0/0"]
    }
}

resource "aws_security_group" "api_lb" {
    name        = "api-load-balancer-security-group"
    description = "api controls access to the ALB"
    vpc_id      = aws_vpc.tracecat.id

    ingress {
        protocol    = "tcp"
        from_port   = "443"
        to_port     = "443"
        cidr_blocks = ["0.0.0.0/0"]
    }

    egress {
        protocol    = "-1"
        from_port   = 0
        to_port     = 0
        cidr_blocks = ["0.0.0.0/0"]
    }
}

resource "aws_security_group" "ecs_tasks" {
    name        = "ecs-tasks-security-group"
    description = "allow inbound access from the ALB only"
    vpc_id      = aws_vpc.tracecat.id

    ingress {
        protocol        = "tcp"
        from_port       = var.app_port
        to_port         = var.app_port
        security_groups = [aws_security_group.ui_lb.id]
    }

    ingress {
        protocol        = "tcp"
        from_port       = 8000 
        to_port         = 8000 
        security_groups = [aws_security_group.api_lb.id]
    }

    egress {
        protocol    = "-1"
        from_port   = 0
        to_port     = 0
        cidr_blocks = ["0.0.0.0/0"]
    }
}


resource "aws_security_group" "temporal_security" {
  name        = "temporal-security"
  description = "Security group for Temporal services"
  vpc_id      = aws_vpc.tracecat.id

  ingress {
    description     = "Allow internal traffic to the Temporal UI service on port 8080"
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    self            = true
  }

  ingress {
    description     = "Allow internal traffic to the Temporal server on port 7233"
    from_port       = 7233 
    to_port         = 7233 
    protocol        = "tcp"
    self            = true
  }

  ingress {
    description     = "Allow internal from Tracecat API service on port 8000"
    from_port       = 8000 
    to_port         = 8000 
    protocol        = "tcp"
    self            = true
  }

  ingress {
    description     = "Allow internal traffic from Tracecat Workder service on port 8001"
    from_port       = 8001 
    to_port         = 8001 
    protocol        = "tcp"
    self            = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "TemporalSecurityGroup"
  }

}

resource "aws_security_group" "core_db_security" {
  name        = "CoreDbSecurityGroup"
  description = "Security group for Tracecat API to RDS communication"
  vpc_id      = aws_vpc.tracecat.id

  ingress {
    description     = "Allow inbound traffic to PostgreSQL database on port 5432"
    from_port       = 5432 
    to_port         = 5432 
    protocol        = "tcp"
    #cidr_blocks = ["10.0.0.0/16"]
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    self     = true 
    from_port       = 0 
    to_port         = 65535 
    protocol        = "tcp"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "CoreDbSecurityGroup"
  }

}

resource "aws_security_group" "core_security" {
  name        = "CoreSecurityGroup"
  description = "Security group for core Tracecat services"
  vpc_id      = aws_vpc.tracecat.id

  ingress {
    description     = "Allow internal traffic to the Tracecat API service on port 8000"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    self            = true
  }

  ingress {
    description     = "Allow internal traffic to the Tracecat Worker service on port 8001"
    from_port       = 8001
    to_port         = 8001
    protocol        = "tcp"
    self            = true
  }

  ingress {
    description     = "Allow internal traffic to the Tracecat UI service on port 3000"
    from_port       = var.app_port 
    to_port         = var.app_port 
    protocol        = "tcp"
    self            = true
  }

  ingress {
    description     = "Allow internal traffic to the Temporal service on port 7233"
    from_port       = 7233 
    to_port         = 7233 
    protocol        = "tcp"
    cidr_blocks     = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "CoreSecurityGroup"
  }
}
