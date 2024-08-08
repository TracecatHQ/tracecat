variable "hosted_zone_id" {
  description = "The ID of the hosted zone in Route53"
  default     = ""
}

variable "cname_record_app" {
  description = "The CNAME record for the tracecat ui"
  default     = "app"
}

variable "cname_record_api" {
  description = "The CNAME record for the exposed tracecat api"
  default     = "api"
}

variable "domain_name" {
  description = "The main domain name"
  default     = ""
}

resource "aws_route53_record" "cname_app" {
  zone_id = var.hosted_zone_id
  name    = "${var.cname_record_app}.${var.domain_name}"
  type    = "CNAME"
  ttl     = "300"
  records = [aws_alb.tracecat_ui.dns_name]
}

resource "aws_route53_record" "cname_api" {
  zone_id = var.hosted_zone_id
  name    = "${var.cname_record_api}.${var.domain_name}"
  type    = "CNAME"
  ttl     = "300"
  records = [aws_alb.tracecat_api.dns_name]
}
