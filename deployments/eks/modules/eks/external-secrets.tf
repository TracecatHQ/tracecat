# External Secrets Operator Installation
# ESO syncs secrets from AWS Secrets Manager into Kubernetes automatically.
# The Tracecat Helm chart creates all ExternalSecret resources (core secrets, postgres,
# redis, temporal). Terraform only manages the ESO operator and ClusterSecretStore.

resource "helm_release" "external_secrets" {
  name             = "external-secrets"
  repository       = "https://charts.external-secrets.io"
  chart            = "external-secrets"
  namespace        = var.external_secrets_namespace
  create_namespace = true
  wait             = true
  timeout          = 600

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
resource "kubernetes_manifest" "external_secrets_cluster_store" {
  manifest = {
    apiVersion = "external-secrets.io/v1"
    kind       = "ClusterSecretStore"
    metadata = {
      name   = local.external_secrets_store_name
      labels = local.common_labels
    }
    spec = {
      provider = {
        aws = {
          service = "SecretsManager"
          region  = local.aws_region
          auth = {
            jwt = {
              serviceAccountRef = {
                name      = var.external_secrets_service_account_name
                namespace = var.external_secrets_namespace
              }
            }
          }
        }
      }
    }
  }

  depends_on = [helm_release.external_secrets]
}

# All ExternalSecret resources are managed by the Helm chart:
# - Core Tracecat secrets (dbEncryptionKey, serviceKey, signingSecret, userAuthSecret)
# - PostgreSQL credentials (with deletionPolicy: Retain to survive upgrades)
# - Redis URL
# - Temporal Cloud API key (when using Temporal Cloud)
#
# Terraform only manages:
# - ESO Helm release (installs the operator)
# - ClusterSecretStore (provides AWS Secrets Manager access)
