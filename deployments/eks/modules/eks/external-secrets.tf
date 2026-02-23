# External Secrets Operator Installation
# ESO syncs secrets from AWS Secrets Manager into Kubernetes automatically.
# The Tracecat Helm chart creates most ExternalSecret resources. Terraform pre-creates
# PostgreSQL credentials so migrations and app startup can rely on a deterministic secret
# path before Helm hook execution.

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

# ExternalSecret for PostgreSQL credentials (needed before Helm installs).
resource "kubernetes_manifest" "postgres_credentials_external_secret" {
  manifest = {
    apiVersion = "external-secrets.io/v1"
    kind       = "ExternalSecret"
    metadata = {
      name      = "tracecat-postgres-secrets"
      namespace = kubernetes_namespace.tracecat.metadata[0].name
      labels    = local.common_labels
    }
    spec = {
      refreshInterval = "1m"
      secretStoreRef = {
        kind = "ClusterSecretStore"
        name = local.external_secrets_store_name
      }
      target = {
        name           = "tracecat-postgres-credentials"
        creationPolicy = "Owner"
      }
      data = [
        {
          secretKey = "username"
          remoteRef = {
            key      = local.rds_master_secret_arn
            property = "username"
          }
        },
        {
          secretKey = "password"
          remoteRef = {
            key      = local.rds_master_secret_arn
            property = "password"
          }
        }
      ]
    }
  }

  depends_on = [kubernetes_manifest.external_secrets_cluster_store, kubernetes_namespace.tracecat]
}

# Note: ExternalSecret resources are created by the Tracecat Helm chart,
# except for PostgreSQL credentials which Terraform creates for deterministic migration startup.
# The chart's externalSecrets.* configuration creates ExternalSecrets for:
# - Core Tracecat secrets (dbEncryptionKey, serviceKey, signingSecret, userAuthSecret)
# - Redis URL
# - Temporal Cloud API key (when using Temporal Cloud)
#
# Terraform only manages:
# - ESO Helm release (installs the operator)
# - ClusterSecretStore (provides AWS Secrets Manager access)
# - PostgreSQL ExternalSecret (all deployment modes)
#
# The Helm chart handles ExternalSecret lifecycle including for Temporal setup.
