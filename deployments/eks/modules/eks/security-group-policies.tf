# SecurityGroupPolicy CRDs are installed by the VPC CNI addon
resource "kubernetes_manifest" "tracecat_postgres_sg_policy" {
  manifest = {
    apiVersion = "vpcresources.k8s.aws/v1beta1"
    kind       = "SecurityGroupPolicy"
    metadata = {
      name      = "tracecat-postgres-access"
      namespace = kubernetes_namespace.tracecat.metadata[0].name
      labels    = local.common_labels
    }
    spec = {
      podSelector = {
        matchLabels = {
          "tracecat.com/access-postgres" = "true"
        }
      }
      securityGroups = {
        groupIds = [aws_security_group.tracecat_postgres_client.id]
      }
    }
  }

  depends_on = [aws_eks_addon.vpc_cni, kubernetes_namespace.tracecat]
}

resource "kubernetes_manifest" "tracecat_redis_sg_policy" {
  manifest = {
    apiVersion = "vpcresources.k8s.aws/v1beta1"
    kind       = "SecurityGroupPolicy"
    metadata = {
      name      = "tracecat-redis-access"
      namespace = kubernetes_namespace.tracecat.metadata[0].name
      labels    = local.common_labels
    }
    spec = {
      podSelector = {
        matchLabels = {
          "tracecat.com/access-redis" = "true"
        }
      }
      securityGroups = {
        groupIds = [aws_security_group.tracecat_redis_client.id]
      }
    }
  }

  depends_on = [aws_eks_addon.vpc_cni, kubernetes_namespace.tracecat]
}
