locals {
  cluster_autoscaler_expander_priorities = merge(
    {
      "50" = ["^eks-${var.cluster_name}-node-group-.*$"]
    },
    var.spot_node_group_enabled ? {
      "100" = ["^eks-${var.cluster_name}-spot-node-group-.*$"]
    } : {}
  )
}

resource "helm_release" "cluster_autoscaler" {
  count = var.cluster_autoscaler_enabled ? 1 : 0

  name       = "cluster-autoscaler"
  repository = "https://kubernetes.github.io/autoscaler"
  chart      = "cluster-autoscaler"
  namespace  = "kube-system"
  version    = var.cluster_autoscaler_chart_version
  wait       = true
  timeout    = 600

  values = [yamlencode({
    autoDiscovery = {
      clusterName = aws_eks_cluster.tracecat.name
    }
    awsRegion = local.aws_region
    rbac = {
      serviceAccount = {
        create = true
        name   = "cluster-autoscaler"
        annotations = {
          "eks.amazonaws.com/role-arn" = try(aws_iam_role.cluster_autoscaler[0].arn, "")
        }
      }
    }
    extraArgs = {
      expander                      = "priority,least-waste"
      "balance-similar-node-groups" = "true"
      "skip-nodes-with-system-pods" = "false"
      "max-node-provision-time"     = "5m"
    }
    expanderPriorities = local.cluster_autoscaler_expander_priorities
  })]

  depends_on = [
    aws_eks_node_group.tracecat,
    aws_eks_node_group.tracecat_spot,
    aws_autoscaling_group_tag.tracecat_on_demand_cluster_autoscaler,
    aws_autoscaling_group_tag.tracecat_spot_cluster_autoscaler,
    aws_iam_role_policy.cluster_autoscaler
  ]
}
