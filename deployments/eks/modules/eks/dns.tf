# ExternalDNS manages Route53 records for Kubernetes ingress resources.
resource "helm_release" "external_dns" {
  name             = "external-dns"
  repository       = "https://kubernetes-sigs.github.io/external-dns/"
  chart            = "external-dns"
  namespace        = var.external_dns_namespace
  create_namespace = true
  wait             = true
  timeout          = 600

  set {
    name  = "provider"
    value = "aws"
  }

  set {
    name  = "policy"
    value = "sync"
  }

  set {
    name  = "registry"
    value = "txt"
  }

  set {
    name  = "txtOwnerId"
    value = var.cluster_name
  }

  set {
    name  = "domainFilters[0]"
    value = var.domain_name
  }

  set {
    name  = "sources[0]"
    value = "ingress"
  }

  set {
    name  = "aws.region"
    value = local.aws_region
  }

  set {
    name  = "serviceAccount.create"
    value = "true"
  }

  set {
    name  = "serviceAccount.name"
    value = var.external_dns_service_account_name
  }

  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.external_dns.arn
  }

  # Only manage A, CNAME, and TXT records. Exclude AAAA to prevent ExternalDNS
  # from creating spurious IPv6 alias records for IPv4-only ALBs, which causes
  # DNS resolution failures for clients that prefer AAAA.
  set {
    name  = "extraArgs[0]"
    value = "--managed-record-types=A"
  }

  set {
    name  = "extraArgs[1]"
    value = "--managed-record-types=CNAME"
  }

  set {
    name  = "extraArgs[2]"
    value = "--managed-record-types=TXT"
  }

  depends_on = [
    aws_eks_node_group.tracecat,
    aws_iam_role_policy.external_dns
  ]
}
