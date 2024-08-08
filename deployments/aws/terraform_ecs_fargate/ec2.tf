resource "aws_security_group" "ec2_target_ssh_ingress" {
  name   = "ec2-target-ssh-ingress"
  vpc_id = aws_vpc.tracecat.id

  # SSH
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port = 0
    to_port   = 0
    protocol  = "-1"
    self      = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}


# Random string for resources
resource "random_string" "suffix" {
  length  = 5
  special = false
  upper   = false
}

locals {
  rs = "${random_string.suffix.id}"
}

resource "tls_private_key" "operator" {
  algorithm = "RSA"
}

module "key_pair" {
  source = "terraform-aws-modules/key-pair/aws"

  key_name   = "operator-${local.rs}"
  public_key = tls_private_key.operator.public_key_openssh
}

# write ssh key to file
resource "local_file" "ssh_key" {
    content  = tls_private_key.operator.private_key_pem
    filename = "${path.module}/ssh_key.pem"
    file_permission = "0700"
}

data "aws_ami" "target_instance" {
  most_recent      = true
  owners           = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_instance" "target_instance" {
  ami           = data.aws_ami.target_instance.id
  #subnet_id     = aws_subnet.private[0].id
  subnet_id     = aws_subnet.public[0].id
  #instance_type = "t2.micro"
  instance_type = "t3a.medium"
  key_name      = module.key_pair.key_pair_name 
  vpc_security_group_ids = [aws_security_group.ec2_target_ssh_ingress.id]
  associate_public_ip_address = true


  tags = {
    Name = "tracecat-ec2"
  }
}

output "Instance_Details" {
  value = <<EOS
----------------
Instance Details
----------------
Instance ID: ${aws_instance.target_instance.id}
Private IP:  ${aws_instance.target_instance.private_ip}
Public IP:   ${aws_instance.target_instance.public_ip}
ssh -i ssh_key.pem ubuntu@${aws_instance.target_instance.public_ip}

EOS
}
