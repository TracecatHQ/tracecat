locals {
  tracecat_spot_scheduling = {
    affinity = {
      nodeAffinity = {
        preferredDuringSchedulingIgnoredDuringExecution = [
          {
            weight = 100
            preference = {
              matchExpressions = [
                {
                  key      = "tracecat.com/capacity"
                  operator = "In"
                  values   = ["spot"]
                }
              ]
            }
          }
        ]
      }
    }
    topologySpreadConstraints = [
      {
        maxSkew           = 1
        topologyKey       = "tracecat.com/capacity"
        whenUnsatisfiable = "ScheduleAnyway"
      }
    ]
  }

  alb_group_name_raw = replace(lower(var.cluster_name), "/[^a-z0-9-]/", "-")
  alb_group_name_compact = trim(
    replace(local.alb_group_name_raw, "/-+/", "-"),
    "-"
  )
  alb_group_name_truncated = substr(local.alb_group_name_compact, 0, 63)
  alb_group_name           = length(trim(local.alb_group_name_truncated, "-")) > 0 ? trim(local.alb_group_name_truncated, "-") : "tracecat"

  tracecat_alb_ingress_annotations = merge(
    {
      "alb.ingress.kubernetes.io/scheme"          = "internet-facing"
      "alb.ingress.kubernetes.io/target-type"     = "ip"
      "alb.ingress.kubernetes.io/listen-ports"    = "[{\"HTTP\": 80}, {\"HTTPS\": 443}]"
      "alb.ingress.kubernetes.io/ssl-redirect"    = "443"
      "alb.ingress.kubernetes.io/certificate-arn" = var.acm_certificate_arn
      "alb.ingress.kubernetes.io/group.name"      = local.alb_group_name
      "external-dns.alpha.kubernetes.io/hostname" = var.domain_name
    },
    var.enable_waf ? {
      "alb.ingress.kubernetes.io/wafv2-acl-arn" = aws_wafv2_web_acl.main[0].arn
    } : {}
  )

  tracecat_alb_listen_ports = jsondecode(local.tracecat_alb_ingress_annotations["alb.ingress.kubernetes.io/listen-ports"])
  tracecat_alb_http_to_https_redirect_enabled = (
    try(tonumber(local.tracecat_alb_ingress_annotations["alb.ingress.kubernetes.io/ssl-redirect"]), 0) == 443 &&
    length([for lp in local.tracecat_alb_listen_ports : lp if lookup(lp, "HTTP", null) == 80]) > 0 &&
    length([for lp in local.tracecat_alb_listen_ports : lp if lookup(lp, "HTTPS", null) == 443]) > 0
  )
}

# Tracecat Helm Release
resource "helm_release" "tracecat" {
  name      = "tracecat"
  chart     = "${path.module}/../../../../helm/tracecat"
  namespace = kubernetes_namespace.tracecat.metadata[0].name

  wait            = true
  wait_for_jobs   = true
  atomic          = true
  cleanup_on_fail = true
  upgrade_install = true
  timeout         = 1500

  # Image tag override
  set {
    name  = "image.tag"
    value = var.tracecat_image_tag
  }

  set {
    name  = "uiImage.tag"
    value = var.tracecat_image_tag
  }

  # Use values for complex nested structures that don't work well with set blocks
  values = [yamlencode(merge(
    {
      ingress = {
        enabled     = true
        split       = var.tracecat_ingress_split
        className   = "alb"
        host        = var.domain_name
        annotations = local.tracecat_alb_ingress_annotations
        ui = {
          annotations = {
            "alb.ingress.kubernetes.io/group.order"             = "20"
            "alb.ingress.kubernetes.io/target-group-attributes" = "stickiness.enabled=true,stickiness.lb_cookie.duration_seconds=86400"
            "alb.ingress.kubernetes.io/healthcheck-path"        = "/"
          }
        }
        api = {
          annotations = {
            "alb.ingress.kubernetes.io/group.order"      = "10"
            "alb.ingress.kubernetes.io/healthcheck-path" = "/api/health"
          }
        }
      }
      urls = {
        publicApp = "https://${var.domain_name}"
        publicApi = "https://${var.domain_name}/api"
      }
      tracecat = {
        auth = {
          types = var.auth_types
        }
        temporal = {
          metrics = {
            enabled = true
            port    = 9000
            path    = "/metrics"
            scrape  = true
          }
        }
      }
      # PostgreSQL TLS configuration with AWS RDS CA certificate
      externalPostgres = {
        tls = {
          verifyCA = true
          caCert   = data.http.rds_ca_bundle.response_body
        }
      }
    },
    var.spot_node_group_enabled ? {
      scheduling = local.tracecat_spot_scheduling
    } : {},
    var.feature_flags != "" ? {
      enterprise = {
        featureFlags = var.feature_flags
      }
    } : {}
  ))]

  # External Secrets Operator Configuration
  # ESO syncs secrets from AWS Secrets Manager - no secrets in TF state
  set {
    name  = "externalSecrets.enabled"
    value = "true"
  }

  set {
    name  = "externalSecrets.clusterSecretStoreRef"
    value = local.external_secrets_store_name
  }

  # Core Tracecat secrets via ESO
  set {
    name  = "externalSecrets.coreSecrets.enabled"
    value = "true"
  }

  set {
    name  = "externalSecrets.coreSecrets.secretArn"
    value = var.tracecat_secrets_arn
  }

  # PostgreSQL credentials via ESO are managed by Terraform (kubernetes_manifest.postgres_credentials_external_secret)
  # so migration hooks do not race Helm-created ExternalSecrets.
  set {
    name  = "externalSecrets.postgres.enabled"
    value = "false"
  }

  # Redis URL via ESO
  set {
    name  = "externalSecrets.redis.enabled"
    value = "true"
  }

  set {
    name  = "externalSecrets.redis.secretArn"
    value = aws_secretsmanager_secret.redis_url.arn
  }

  # Service account (IRSA) for S3 access
  set {
    name  = "serviceAccount.create"
    value = "true"
  }

  set {
    name  = "serviceAccount.name"
    value = local.tracecat_service_account_name
  }

  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.tracecat_s3.arn
  }

  # Superadmin
  set {
    name  = "tracecat.auth.superadminEmail"
    value = var.superadmin_email
  }

  # Replica counts
  set {
    name  = "api.replicas"
    value = var.api_replicas
  }

  set {
    name  = "worker.replicas"
    value = var.worker_replicas
  }

  set {
    name  = "executor.replicas"
    value = var.executor_replicas
  }

  set {
    name  = "executor.queue"
    value = var.executor_queue
  }

  set {
    name  = "executor.backend"
    value = var.executor_backend
  }

  # Agent Executor configuration
  set {
    name  = "agentExecutor.replicas"
    value = var.agent_executor_replicas
  }

  set {
    name  = "agentExecutor.queue"
    value = var.agent_executor_queue
  }

  set {
    name  = "agentExecutor.backend"
    value = var.agent_executor_backend
  }

  set {
    name  = "ui.replicas"
    value = var.ui_replicas
  }

  # Production resource requests and limits
  set {
    name  = "api.resources.requests.cpu"
    value = "${var.api_cpu_request_millicores}m"
  }

  set {
    name  = "api.resources.requests.memory"
    value = "${var.api_memory_request_mib}Mi"
  }

  set {
    name  = "api.resources.limits.cpu"
    value = "${var.api_cpu_request_millicores}m"
  }

  set {
    name  = "api.resources.limits.memory"
    value = "${var.api_memory_request_mib}Mi"
  }

  set {
    name  = "worker.resources.requests.cpu"
    value = "${var.worker_cpu_request_millicores}m"
  }

  set {
    name  = "worker.resources.requests.memory"
    value = "${var.worker_memory_request_mib}Mi"
  }

  set {
    name  = "worker.resources.limits.cpu"
    value = "${var.worker_cpu_request_millicores}m"
  }

  set {
    name  = "worker.resources.limits.memory"
    value = "${var.worker_memory_request_mib}Mi"
  }

  set {
    name  = "executor.resources.requests.cpu"
    value = "${var.executor_cpu_request_millicores}m"
  }

  set {
    name  = "executor.resources.requests.memory"
    value = "${var.executor_memory_request_mib}Mi"
  }

  set {
    name  = "executor.resources.limits.cpu"
    value = "${var.executor_cpu_request_millicores}m"
  }

  set {
    name  = "executor.resources.limits.memory"
    value = "${var.executor_memory_request_mib}Mi"
  }

  set {
    name  = "agentExecutor.resources.requests.cpu"
    value = "${var.agent_executor_cpu_request_millicores}m"
  }

  set {
    name  = "agentExecutor.resources.requests.memory"
    value = "${var.agent_executor_memory_request_mib}Mi"
  }

  set {
    name  = "agentExecutor.resources.limits.cpu"
    value = "${var.agent_executor_cpu_request_millicores}m"
  }

  set {
    name  = "agentExecutor.resources.limits.memory"
    value = "${var.agent_executor_memory_request_mib}Mi"
  }

  set {
    name  = "ui.resources.requests.cpu"
    value = "${var.ui_cpu_request_millicores}m"
  }

  set {
    name  = "ui.resources.requests.memory"
    value = "${var.ui_memory_request_mib}Mi"
  }

  set {
    name  = "ui.resources.limits.cpu"
    value = "${var.ui_cpu_request_millicores}m"
  }

  set {
    name  = "ui.resources.limits.memory"
    value = "${var.ui_memory_request_mib}Mi"
  }

  # External PostgreSQL (RDS)
  set {
    name  = "externalPostgres.host"
    value = aws_db_instance.tracecat.address
  }

  set {
    name  = "externalPostgres.port"
    value = "5432"
  }

  set {
    name  = "externalPostgres.database"
    value = "tracecat"
  }

  set {
    name  = "externalPostgres.sslMode"
    value = "require"
  }

  # Use the ESO-synced Kubernetes secret so pre-install migrations don't depend on IRSA.
  set {
    name  = "externalPostgres.auth.existingSecret"
    value = "tracecat-postgres-credentials"
  }

  # External Redis (ElastiCache)
  # ESO creates the secret; reference by target name
  set {
    name  = "externalRedis.auth.existingSecret"
    value = "tracecat-redis-credentials"
  }

  # External S3 (uses IRSA - don't set endpoint to use default credential chain)
  set {
    name  = "externalS3.region"
    value = local.aws_region
  }


  set {
    name  = "tracecat.blobStorage.buckets.attachments"
    value = local.s3_attachments_bucket
  }

  set {
    name  = "tracecat.blobStorage.buckets.registry"
    value = local.s3_registry_bucket
  }

  set {
    name  = "tracecat.blobStorage.buckets.workflow"
    value = local.s3_workflow_bucket
  }

  # Temporal Configuration
  set {
    name  = "temporal.enabled"
    value = var.temporal_mode == "self-hosted" ? "true" : "false"
  }

  # Self-hosted Temporal with RDS
  set {
    name  = "temporal.server.podLabels.tracecat\\.com/access-postgres"
    value = "true"
  }

  # Mount the RDS CA certificate into Temporal server pods for TLS verification
  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.additionalVolumes[0].name"
      value = "postgres-ca"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.additionalVolumes[0].configMap.name"
      value = "tracecat-postgres-ca"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.additionalVolumeMounts[0].name"
      value = "postgres-ca"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.additionalVolumeMounts[0].mountPath"
      value = "/etc/tracecat/certs/postgres"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.additionalVolumeMounts[0].readOnly"
      value = "true"
    }
  }

  # Mount the RDS CA certificate into Temporal admintools/schema job for TLS verification
  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.admintools.additionalVolumes[0].name"
      value = "postgres-ca"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.admintools.additionalVolumes[0].configMap.name"
      value = "tracecat-postgres-ca"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.admintools.additionalVolumeMounts[0].name"
      value = "postgres-ca"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.admintools.additionalVolumeMounts[0].mountPath"
      value = "/etc/tracecat/certs/postgres"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.admintools.additionalVolumeMounts[0].readOnly"
      value = "true"
    }
  }

  # Default datastore (temporal database)
  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.default.sql.pluginName"
      value = "postgres12"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.default.sql.databaseName"
      value = "temporal"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.default.sql.connectAddr"
      value = "${aws_db_instance.tracecat.address}:5432"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.default.sql.user"
      value = var.rds_master_username
    }
  }

  # Temporal uses the ESO-created postgres secret
  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.default.sql.existingSecret"
      value = "tracecat-postgres-credentials"
    }
  }

  # Visibility datastore (temporal_visibility database)
  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.visibility.sql.pluginName"
      value = "postgres12"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.visibility.sql.databaseName"
      value = "temporal_visibility"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.visibility.sql.connectAddr"
      value = "${aws_db_instance.tracecat.address}:5432"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.visibility.sql.user"
      value = var.rds_master_username
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.visibility.sql.existingSecret"
      value = "tracecat-postgres-credentials"
    }
  }

  # PostgreSQL TLS configuration for Temporal (required for RDS)
  # Use tls.enabled instead of connectAttributes.sslmode to avoid conflict with tool's default sslmode=disable
  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.default.sql.tls.enabled"
      value = "true"
    }
  }

  # Enable TLS host verification for RDS endpoints using the AWS RDS CA bundle.
  # The CA certificate is mounted via externalPostgres.tls.caCert in the Helm values.
  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.default.sql.tls.enableHostVerification"
      value = "true"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.default.sql.tls.caFile"
      value = "/etc/tracecat/certs/postgres/ca-bundle.pem"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.visibility.sql.tls.enabled"
      value = "true"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.visibility.sql.tls.enableHostVerification"
      value = "true"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "self-hosted" ? [1] : []
    content {
      name  = "temporal.server.config.persistence.datastores.visibility.sql.tls.caFile"
      value = "/etc/tracecat/certs/postgres/ca-bundle.pem"
    }
  }

  # External Temporal cluster configuration
  dynamic "set" {
    for_each = var.temporal_mode == "cloud" ? [1] : []
    content {
      name  = "externalTemporal.enabled"
      value = "true"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "cloud" ? [1] : []
    content {
      name  = "externalTemporal.clusterUrl"
      value = var.temporal_cluster_url
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "cloud" ? [1] : []
    content {
      name  = "externalTemporal.clusterNamespace"
      value = var.temporal_cluster_namespace
    }
  }

  # External cluster API key via ESO
  dynamic "set" {
    for_each = var.temporal_mode == "cloud" && var.temporal_secret_arn != "" ? [1] : []
    content {
      name  = "externalSecrets.temporal.enabled"
      value = "true"
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "cloud" && var.temporal_secret_arn != "" ? [1] : []
    content {
      name  = "externalSecrets.temporal.secretArn"
      value = var.temporal_secret_arn
    }
  }

  dynamic "set" {
    for_each = var.temporal_mode == "cloud" && var.temporal_secret_arn != "" ? [1] : []
    content {
      name  = "externalTemporal.auth.existingSecret"
      value = "tracecat-temporal-credentials"
    }
  }

  set {
    name  = "enterprise.multiTenant"
    value = var.ee_multi_tenant
  }

  # OIDC Configuration
  dynamic "set" {
    for_each = var.oidc_issuer != "" ? [1] : []
    content {
      name  = "tracecat.oidc.issuer"
      value = var.oidc_issuer
    }
  }

  dynamic "set" {
    for_each = var.oidc_client_id != "" ? [1] : []
    content {
      name  = "tracecat.oidc.clientId"
      value = var.oidc_client_id
    }
  }

  dynamic "set_sensitive" {
    for_each = var.oidc_client_secret != "" ? [1] : []
    content {
      name  = "tracecat.oidc.clientSecret"
      value = var.oidc_client_secret
    }
  }

  dynamic "set" {
    for_each = var.oidc_scopes != "" ? [1] : []
    content {
      name  = "tracecat.oidc.scopes"
      value = var.oidc_scopes
      type  = "string"
    }
  }

  depends_on = [
    aws_eks_node_group.tracecat,
    aws_eks_node_group.tracecat_spot,
    helm_release.aws_load_balancer_controller,
    kubernetes_manifest.external_secrets_cluster_store,
    kubernetes_manifest.postgres_credentials_external_secret,
    aws_secretsmanager_secret_version.redis_url,
    kubernetes_job_v1.create_temporal_databases
  ]
}
