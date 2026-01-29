# Stakater Reloader - Automatically restarts pods when secrets change
# https://github.com/stakater/Reloader
#
# This is critical for handling AWS Secrets Manager rotation (e.g., RDS password
# rotation every 7 days). When External Secrets Operator syncs updated credentials,
# Reloader triggers rolling restarts so pods pick up the new values.

resource "helm_release" "reloader" {
  name             = "reloader"
  repository       = "https://stakater.github.io/stakater-charts"
  chart            = "reloader"
  version          = "2.2.7" # Chart version for app v1.4.12
  namespace        = "reloader"
  create_namespace = true

  set {
    name  = "reloader.watchGlobally"
    value = "true"
  }

  set {
    name  = "reloader.deployment.replicas"
    value = "1"
  }

  # Resource limits for security
  set {
    name  = "reloader.deployment.resources.limits.cpu"
    value = "100m"
  }

  set {
    name  = "reloader.deployment.resources.limits.memory"
    value = "128Mi"
  }

  set {
    name  = "reloader.deployment.resources.requests.cpu"
    value = "10m"
  }

  set {
    name  = "reloader.deployment.resources.requests.memory"
    value = "64Mi"
  }

  depends_on = [aws_eks_node_group.tracecat]
}
