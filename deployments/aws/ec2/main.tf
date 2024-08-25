provider "aws" {
  region = var.aws_region
}

data "aws_ami" "this" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*-x86_64-gp2"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  project_tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.project_name}-vpc"
  cidr = var.vpc_cidr

  azs             = [var.aws_availability_zone]
  private_subnets = [var.private_subnet_cidr]
  public_subnets  = [var.public_subnet_cidr]

  enable_dns_hostnames = true
  enable_dns_support   = true
  enable_nat_gateway   = true
  single_nat_gateway   = true

  tags = merge(local.project_tags, {
    Name = "${var.project_name}-vpc"
  })
}

resource "aws_vpc_endpoint" "ssm" {
  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.ssm"
  vpc_endpoint_type   = "Interface"
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  subnet_ids          = module.vpc.private_subnets
  private_dns_enabled = true

  tags = merge(local.project_tags, {
    Name = "${var.project_name}-ssm-endpoint"
  })
}

resource "aws_vpc_endpoint" "ec2messages" {
  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.ec2messages"
  vpc_endpoint_type   = "Interface"
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  subnet_ids          = module.vpc.private_subnets
  private_dns_enabled = true

  tags = merge(local.project_tags, {
    Name = "${var.project_name}-ec2messages-endpoint"
  })
}

resource "aws_vpc_endpoint" "ssmmessages" {
  vpc_id              = module.vpc.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.ssmmessages"
  vpc_endpoint_type   = "Interface"
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  subnet_ids          = module.vpc.private_subnets
  private_dns_enabled = true

  tags = merge(local.project_tags, {
    Name = "${var.project_name}-ssmmessages-endpoint"
  })
}

resource "aws_security_group" "vpc_endpoints" {
  name        = "${var.project_name}-vpc-endpoints-sg"
  description = "Security group for VPC endpoints"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "Allow HTTPS from VPC CIDR"
  }

  tags = merge(local.project_tags, {
    Name = "${var.project_name}-vpc-endpoints-sg"
  })
}

resource "aws_security_group" "this" {
  name        = "${var.project_name}-sg"
  description = "Security group for ${var.project_name} EC2 instance"
  vpc_id      = module.vpc.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "Caddy HTTP server"
  }

  tags = merge(local.project_tags, {
    Name = "${var.project_name}-sg"
  })
}

resource "aws_iam_role" "this" {
  name = "${var.project_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = local.project_tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  role       = aws_iam_role.this.name
}

resource "aws_iam_instance_profile" "this" {
  name = "${var.project_name}-profile"
  role = aws_iam_role.this.name

  tags = local.project_tags
}

# Security group for EFS
resource "aws_security_group" "efs" {
  name        = "${var.project_name}-efs-sg"
  description = "Allow EFS access from EC2 instances"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.this.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.project_tags, {
    Name = "${var.project_name}-efs-sg"
  })
}

# Add EFS File System
resource "aws_efs_file_system" "this" {
  creation_token = "${var.project_name}-efs"
  encrypted      = true

  tags = merge(local.project_tags, {
    Name = "${var.project_name}-efs"
  })
}

# Add EFS Mount Target
resource "aws_efs_mount_target" "this" {
  file_system_id  = aws_efs_file_system.this.id
  subnet_id       = module.vpc.private_subnets[0]
  security_groups = [aws_security_group.efs.id]
}

# EC2 instance
resource "aws_instance" "this" {
  ami                    = data.aws_ami.this.id
  instance_type          = var.instance_type
  subnet_id              = module.vpc.private_subnets[0]
  vpc_security_group_ids = [aws_security_group.this.id]
  iam_instance_profile   = aws_iam_instance_profile.this.name
  availability_zone      = var.aws_availability_zone

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  user_data = base64encode(templatefile("${path.module}/user_data.tpl", {
    tracecat_version = var.tracecat_version
    efs_id           = aws_efs_file_system.this.id
  }))

  provisioner "local-exec" {
    command = <<-EOT
      aws ec2 wait instance-status-ok --instance-ids ${self.id} --region ${var.aws_region} && \
      sleep 60 && \
      aws ssm send-command \
        --instance-ids ${self.id} \
        --document-name "AWS-RunShellScript" \
        --parameters '{"commands":["cat /var/log/user-data.log"]}' \
        --output text \
        --region ${var.aws_region} \
        --query "Command.CommandId" > ssm_command_id.txt && \
      sleep 10 && \
      aws ssm get-command-invocation \
        --command-id $(cat ssm_command_id.txt) \
        --instance-id ${self.id} \
        --query "StandardOutputContent" \
        --region ${var.aws_region} \
        --output text > user_data_log.txt && \
      if grep -q "ERROR:" user_data_log.txt; then
        echo "Error detected in user data log. Log content:"
        cat user_data_log.txt
        exit 1
      else
        echo "User data script completed successfully"
      fi
    EOT
  }

  tags = merge(local.project_tags, {
    Name = "${var.project_name}-instance"
  })
}

# Update IAM role to allow EFS access
resource "aws_iam_role_policy" "efs_access" {
  name = "efs_access"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "elasticfilesystem:ClientMount",
          "elasticfilesystem:ClientWrite"
        ]
        Resource = aws_efs_file_system.this.arn
      }
    ]
  })
}