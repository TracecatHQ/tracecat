# Route53 DNS Record for ALB Ingress
# The ALB is created by the AWS Load Balancer Controller based on the Ingress resource.

data "kubernetes_ingress_v1" "tracecat" {
  metadata {
    name      = "tracecat"
    namespace = kubernetes_namespace.tracecat.metadata[0].name
  }

  depends_on = [
    helm_release.aws_load_balancer_controller,
    helm_release.tracecat
  ]
}

data "aws_elb_hosted_zone_id" "alb" {}

locals {
  tracecat_ingress_hostname = try(data.kubernetes_ingress_v1.tracecat.status[0].load_balancer[0].ingress[0].hostname, "")
}

resource "aws_route53_record" "tracecat" {
  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = local.tracecat_ingress_hostname
    zone_id                = data.aws_elb_hosted_zone_id.alb.id
    evaluate_target_health = true
  }

  lifecycle {
    precondition {
      condition     = local.tracecat_ingress_hostname != ""
      error_message = "Tracecat ingress does not have a load balancer hostname yet. Re-apply after the ALB is provisioned."
    }
  }

  depends_on = [data.kubernetes_ingress_v1.tracecat]
}
