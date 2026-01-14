# ACM Certificate for the domain
resource "aws_acm_certificate" "tracecat" {
  domain_name       = var.domain_name
  validation_method = "DNS"

  tags = merge(var.tags, {
    Name = "tracecat-certificate"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# Route53 DNS validation records
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.tracecat.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = var.hosted_zone_id
}

# Certificate validation
resource "aws_acm_certificate_validation" "tracecat" {
  certificate_arn         = aws_acm_certificate.tracecat.arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}
