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

  # Use IAM access entry API exclusively for cluster auth. Avoids the
  # aws-auth ConfigMap attack surface (any kube-system ConfigMap writer
  # can escalate to cluster admin). Access entries are IAM-controlled
  # and CloudTrail-audited.
  access_config {
    authentication_mode                         = "API_AND_CONFIG_MAP"
    bootstrap_cluster_creator_admin_permissions = true
  }

  # Enable logging for audit and API server
  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
    aws_iam_role_policy_attachment.eks_vpc_resource_controller
  ]
}

# In pod-eni mode, pods attached to custom SecurityGroupPolicy SGs must still reach
# CoreDNS endpoints on worker-node ENIs (cluster security group).
# Use static keys so for_each is known at plan time (SG IDs are only known at apply).
# This avoids the "Invalid for_each argument" error on first deployment.
resource "aws_security_group_rule" "cluster_dns_from_tracecat_pod_sgs_udp" {
  for_each = {
    postgres = aws_security_group.tracecat_postgres_client.id
    redis    = aws_security_group.tracecat_redis_client.id
  }

  type                     = "ingress"
  from_port                = 53
  to_port                  = 53
  protocol                 = "udp"
  security_group_id        = aws_eks_cluster.tracecat.vpc_config[0].cluster_security_group_id
  source_security_group_id = each.value
  description              = "Allow UDP DNS from Tracecat pod SGs"
}

resource "aws_security_group_rule" "cluster_dns_from_tracecat_pod_sgs_tcp" {
  for_each = {
    postgres = aws_security_group.tracecat_postgres_client.id
    redis    = aws_security_group.tracecat_redis_client.id
  }

  type                     = "ingress"
  from_port                = 53
  to_port                  = 53
  protocol                 = "tcp"
  security_group_id        = aws_eks_cluster.tracecat.vpc_config[0].cluster_security_group_id
  source_security_group_id = each.value
  description              = "Allow TCP DNS from Tracecat pod SGs"
}

# EKS Add-ons (Core cluster components)
resource "aws_eks_addon" "vpc_cni" {
  cluster_name = aws_eks_cluster.tracecat.name
  addon_name   = "vpc-cni"

  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  configuration_values = jsonencode({
    env = {
      # Pod ENI is required for SecurityGroupPolicy-based isolation.
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

  capacity_type  = "ON_DEMAND"
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
  ami_type = var.node_ami_type

  labels = {
    "tracecat.com/purpose"  = "tracecat"
    "tracecat.com/capacity" = "on-demand"
  }

  tags = var.tags

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry_read
  ]
}

resource "aws_eks_node_group" "tracecat_spot" {
  count = var.spot_node_group_enabled ? 1 : 0

  cluster_name    = aws_eks_cluster.tracecat.name
  node_group_name = "${var.cluster_name}-spot-node-group"
  node_role_arn   = aws_iam_role.eks_node_group.arn
  subnet_ids      = var.private_subnet_ids

  capacity_type  = "SPOT"
  instance_types = var.spot_node_instance_types
  disk_size      = var.node_disk_size

  scaling_config {
    desired_size = var.spot_node_desired_size
    min_size     = var.spot_node_min_size
    max_size     = var.spot_node_max_size
  }

  update_config {
    max_unavailable = 1
  }

  ami_type = var.node_ami_type

  labels = {
    "tracecat.com/purpose"  = "tracecat"
    "tracecat.com/capacity" = "spot"
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
