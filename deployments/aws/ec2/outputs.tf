output "instance_id" {
  description = "The ID of the EC2 instance"
  value       = aws_instance.this.id
}

output "instance_private_ip" {
  description = "The private IP address of the EC2 instance"
  value       = aws_instance.this.private_ip
}

output "vpc_id" {
  description = "The ID of the VPC"
  value       = module.vpc.vpc_id
}

output "security_group_id" {
  description = "The ID of the instance security group"
  value       = aws_security_group.this.id
}

output "nat_gateway_id" {
  value = module.vpc.natgw_ids[0]
}
