# SecurityGroupPolicy CRDs are installed by the VPC CNI addon
# Using null_resource with kubectl because kubernetes_manifest has CRD timing issues

resource "null_resource" "tracecat_postgres_sg_policy" {
  triggers = {
    namespace      = kubernetes_namespace.tracecat.metadata[0].name
    security_group = aws_security_group.tracecat_postgres_client.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      cat <<EOF | kubectl apply -f -
apiVersion: vpcresources.k8s.aws/v1beta1
kind: SecurityGroupPolicy
metadata:
  name: tracecat-postgres-access
  namespace: ${kubernetes_namespace.tracecat.metadata[0].name}
  labels:
    app.kubernetes.io/managed-by: terraform
    app.kubernetes.io/part-of: tracecat
spec:
  podSelector:
    matchLabels:
      tracecat.com/access-postgres: "true"
  securityGroups:
    groupIds:
      - ${aws_security_group.tracecat_postgres_client.id}
EOF
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = "kubectl delete securitygrouppolicy tracecat-postgres-access -n ${self.triggers.namespace} --ignore-not-found"
  }

  depends_on = [aws_eks_addon.vpc_cni, kubernetes_namespace.tracecat]
}

resource "null_resource" "tracecat_redis_sg_policy" {
  triggers = {
    namespace      = kubernetes_namespace.tracecat.metadata[0].name
    security_group = aws_security_group.tracecat_redis_client.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      cat <<EOF | kubectl apply -f -
apiVersion: vpcresources.k8s.aws/v1beta1
kind: SecurityGroupPolicy
metadata:
  name: tracecat-redis-access
  namespace: ${kubernetes_namespace.tracecat.metadata[0].name}
  labels:
    app.kubernetes.io/managed-by: terraform
    app.kubernetes.io/part-of: tracecat
spec:
  podSelector:
    matchLabels:
      tracecat.com/access-redis: "true"
  securityGroups:
    groupIds:
      - ${aws_security_group.tracecat_redis_client.id}
EOF
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = "kubectl delete securitygrouppolicy tracecat-redis-access -n ${self.triggers.namespace} --ignore-not-found"
  }

  depends_on = [aws_eks_addon.vpc_cni, kubernetes_namespace.tracecat]
}
