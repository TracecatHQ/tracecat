# EKS Cluster Security Group
resource "aws_security_group" "eks_cluster" {
  name        = "${var.cluster_name}-cluster-sg"
  description = "Security group for EKS cluster"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-cluster-sg"
  })
}

# EKS Cluster
resource "aws_eks_cluster" "tracecat" {
  name     = var.cluster_name
  version  = var.cluster_version
  role_arn = aws_iam_role.eks_cluster.arn

  vpc_config {
    subnet_ids              = concat(var.private_subnet_ids, var.public_subnet_ids)
    endpoint_private_access = true
    endpoint_public_access  = true
    security_group_ids      = [aws_security_group.eks_cluster.id]
  }

  # Enable logging for audit and API server
  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
    aws_iam_role_policy_attachment.eks_vpc_resource_controller
  ]
}

# EKS Add-ons (Core cluster components)
resource "aws_eks_addon" "vpc_cni" {
  cluster_name = aws_eks_cluster.tracecat.name
  addon_name   = "vpc-cni"

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  configuration_values = jsonencode({
    env = {
      ENABLE_POD_ENI                    = "true"
      POD_SECURITY_GROUP_ENFORCING_MODE = "standard"
    }
  })

  tags = var.tags
}

resource "aws_eks_addon" "coredns" {
  cluster_name = aws_eks_cluster.tracecat.name
  addon_name   = "coredns"

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  tags = var.tags

  depends_on = [aws_eks_node_group.tracecat]
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name = aws_eks_cluster.tracecat.name
  addon_name   = "kube-proxy"

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  tags = var.tags
}

# EKS Managed Node Group
resource "aws_eks_node_group" "tracecat" {
  cluster_name    = aws_eks_cluster.tracecat.name
  node_group_name = "${var.cluster_name}-node-group"
  node_role_arn   = aws_iam_role.eks_node_group.arn
  subnet_ids      = var.private_subnet_ids

  instance_types = var.node_instance_types
  disk_size      = var.node_disk_size

  scaling_config {
    desired_size = var.node_desired_size
    min_size     = var.node_min_size
    max_size     = var.node_max_size
  }

  update_config {
    max_unavailable = 1
  }

  # AL2023_ARM_64_STANDARD for Graviton (t4g) instances
  # AL2023_x86_64_STANDARD for Intel/AMD (t3, m5, etc.) instances
  ami_type = length(regexall("^t4g|^m6g|^c6g|^r6g", var.node_instance_types[0])) > 0 ? "AL2023_ARM_64_STANDARD" : "AL2023_x86_64_STANDARD"

  labels = {
    "tracecat.com/purpose" = "tracecat"
  }

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry_read
  ]
}

# Kubernetes and Helm Provider Configuration
data "aws_eks_cluster_auth" "tracecat" {
  name = aws_eks_cluster.tracecat.name
}
