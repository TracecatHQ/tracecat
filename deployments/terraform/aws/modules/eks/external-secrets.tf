# External Secrets Operator Installation
# ESO syncs secrets from AWS Secrets Manager into Kubernetes automatically.
# The Tracecat Helm chart creates ExternalSecret resources for all needed secrets.

resource "helm_release" "external_secrets" {
  name             = "external-secrets"
  repository       = "https://charts.external-secrets.io"
  chart            = "external-secrets"
  namespace        = var.external_secrets_namespace
  create_namespace = true

  set {
    name  = "installCRDs"
    value = "true"
  }

  set {
    name  = "serviceAccount.create"
    value = "true"
  }

  set {
    name  = "serviceAccount.name"
    value = var.external_secrets_service_account_name
  }

  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.external_secrets.arn
  }
}

# ClusterSecretStore for AWS Secrets Manager
# This is referenced by the Helm chart's ExternalSecret resources
# Using null_resource with kubectl because kubernetes_manifest has CRD timing issues
resource "null_resource" "external_secrets_cluster_store" {
  # Triggers ensure re-creation if any of these values change
  triggers = {
    store_name = local.external_secrets_store_name
    region     = local.aws_region
    sa_name    = var.external_secrets_service_account_name
    namespace  = var.external_secrets_namespace
  }

  provisioner "local-exec" {
    command = <<-EOT
      # Wait for CRDs to be registered (up to 60 seconds)
      for i in $(seq 1 12); do
        if kubectl get crd clustersecretstores.external-secrets.io >/dev/null 2>&1; then
          echo "CRD found, applying ClusterSecretStore..."
          break
        fi
        echo "Waiting for External Secrets CRDs to be registered... ($i/12)"
        sleep 5
      done

      cat <<EOF | kubectl apply -f -
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: ${local.external_secrets_store_name}
  labels:
    app.kubernetes.io/managed-by: terraform
    app.kubernetes.io/part-of: tracecat
spec:
  provider:
    aws:
      service: SecretsManager
      region: ${local.aws_region}
      auth:
        jwt:
          serviceAccountRef:
            name: ${var.external_secrets_service_account_name}
            namespace: ${var.external_secrets_namespace}
EOF
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = "kubectl delete clustersecretstore ${self.triggers.store_name} --ignore-not-found"
  }

  depends_on = [helm_release.external_secrets]
}

# Note: All ExternalSecret resources are created by the Tracecat Helm chart.
# The chart's externalSecrets.* configuration creates ExternalSecrets for:
# - Core Tracecat secrets (dbEncryptionKey, serviceKey, signingSecret, userAuthSecret)
# - PostgreSQL credentials (username, password)
# - Redis URL
# - Temporal Cloud API key (when using Temporal Cloud)
#
# Terraform only manages:
# - ESO Helm release (installs the operator)
# - ClusterSecretStore (provides AWS Secrets Manager access)
#
# The Helm chart handles ExternalSecret lifecycle including for Temporal setup.
