output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.tracecat.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets"
  value       = aws_subnet.private[*].id
}

output "private_route_table_ids" {
  description = "IDs of the private route tables"
  value       = aws_route_table.private[*].id
}

output "acm_certificate_arn" {
  description = "ARN of the validated ACM certificate"
  value       = aws_acm_certificate_validation.tracecat.certificate_arn
}

output "nat_gateway_eips" {
  description = "Public Elastic IPs attached to NAT gateways for outbound traffic"
  value       = aws_eip.nat[*].public_ip
}
