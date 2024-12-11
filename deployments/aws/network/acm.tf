resource "aws_acm_certificate" "tracecat" {
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "tracecat" {
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

resource "aws_acm_certificate_validation" "tracecat" {
  certificate_arn         = aws_acm_certificate.tracecat.arn
  validation_record_fqdns = [for record in aws_route53_record.tracecat : record.fqdn]
}

resource "aws_acm_certificate" "temporal_ui" {
  domain_name       = var.temporal_ui_domain_name
  validation_method = "DNS"
}

resource "aws_route53_record" "temporal_ui" {
  for_each = {
    for dvo in aws_acm_certificate.temporal_ui.domain_validation_options : dvo.domain_name => {
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
  zone_id         = var.temporal_ui_hosted_zone_id
}

resource "aws_acm_certificate_validation" "temporal_ui" {
  certificate_arn         = aws_acm_certificate.temporal_ui.arn
  validation_record_fqdns = [for record in aws_route53_record.temporal_ui : record.fqdn]
}
