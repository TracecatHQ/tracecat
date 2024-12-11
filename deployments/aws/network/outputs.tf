output "vpc_id" {
  value = aws_vpc.tracecat.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "acm_certificate_arn" {
  value = aws_acm_certificate.this.arn
}
