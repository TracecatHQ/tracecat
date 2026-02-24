# Grafana Cloud Observability Pipeline
#
# This file provisions:
# 1. Grafana K8s Monitoring Helm chart (Alloy-based) for in-cluster metrics + logs
# 2. CloudWatch Metric Streams + Kinesis Firehose for AWS service metrics
# 3. ESO ExternalSecret for syncing Grafana Cloud credentials to the cluster
# 4. IAM roles with confused-deputy protections
#
# Gated by var.enable_observability (master toggle).

locals {
  observability_enabled = var.enable_observability
}

# -----------------------------------------------------------------------------
# Observability Namespace
# -----------------------------------------------------------------------------

resource "kubernetes_namespace" "observability" {
  count = local.observability_enabled ? 1 : 0

  metadata {
    name   = "observability"
    labels = local.common_labels
  }
}

# -----------------------------------------------------------------------------
# ESO: Sync Grafana Cloud credentials to cluster
# -----------------------------------------------------------------------------

resource "kubernetes_manifest" "grafana_cloud_external_secret" {
  count = local.observability_enabled ? 1 : 0

  manifest = {
    apiVersion = "external-secrets.io/v1"
    kind       = "ExternalSecret"
    metadata = {
      name      = "grafana-cloud-credentials"
      namespace = kubernetes_namespace.observability[0].metadata[0].name
      labels    = local.common_labels
    }
    spec = {
      refreshInterval = "1h"
      secretStoreRef = {
        kind = "ClusterSecretStore"
        name = local.external_secrets_store_name
      }
      target = {
        name           = "grafana-cloud-credentials"
        creationPolicy = "Owner"
      }
      data = [
        {
          secretKey = "metrics-write-token"
          remoteRef = {
            key      = var.grafana_cloud_credentials_secret_arn
            property = "metrics_write_token"
          }
        }
      ]
    }
  }

  depends_on = [
    kubernetes_manifest.external_secrets_cluster_store,
    kubernetes_namespace.observability,
  ]
}

# -----------------------------------------------------------------------------
# Grafana K8s Monitoring Helm Chart
# -----------------------------------------------------------------------------

resource "helm_release" "grafana_k8s_monitoring" {
  count = local.observability_enabled ? 1 : 0

  name             = "grafana-k8s-monitoring"
  repository       = "https://grafana.github.io/helm-charts"
  chart            = "k8s-monitoring"
  version          = "2.0.6"
  namespace        = kubernetes_namespace.observability[0].metadata[0].name
  create_namespace = false
  wait             = true
  timeout          = 600

  values = [yamlencode({
    cluster = {
      name = var.cluster_name
    }

    # Destinations (v2 chart pattern)
    destinations = [
      {
        name = "grafana-cloud-metrics"
        type = "prometheus"
        url  = var.grafana_cloud_prometheus_url
        auth = {
          type     = "basic"
          username = var.grafana_cloud_prometheus_username
          passwordFrom = {
            secretKeyRef = {
              name = "grafana-cloud-credentials"
              key  = "metrics-write-token"
            }
          }
        }
      },
      {
        name = "grafana-cloud-logs"
        type = "loki"
        url  = var.grafana_cloud_loki_url
        auth = {
          type     = "basic"
          username = var.grafana_cloud_loki_username
          passwordFrom = {
            secretKeyRef = {
              name = "grafana-cloud-credentials"
              key  = "metrics-write-token"
            }
          }
        }
      },
    ]

    # Metrics collection
    clusterMetrics = {
      enabled = true
      node-exporter = {
        enabled = true
      }
      kube-state-metrics = {
        enabled = true
      }
      kubelet = {
        enabled = true
      }
      cadvisor = {
        enabled = true
      }
    }

    # Annotation autodiscovery for Tracecat and KEDA Prometheus endpoints.
    # Discovers pods/services with prometheus.io/scrape=true annotations.
    annotationAutodiscovery = {
      enabled = true
      annotations = {
        scrape            = "prometheus.io/scrape"
        metricsPortNumber = "prometheus.io/port"
        metricsPath       = "prometheus.io/path"
      }
    }

    # Log collection
    clusterEvents = {
      enabled = true
    }

    podLogs = {
      enabled = true
      namespaces = [
        "tracecat",
        "observability",
      ]
    }

    # Disable features we don't need yet
    annotationAutodiscovery_profiles = {
      enabled = false
    }

    # Cost metrics (OpenCost) - skip for now
    cost = {
      enabled = false
    }

    # Profiles - skip for now
    profiles = {
      enabled = false
    }
  })]

  depends_on = [
    kubernetes_manifest.grafana_cloud_external_secret,
  ]
}

# -----------------------------------------------------------------------------
# CloudWatch Metric Streams -> Grafana Cloud (via Kinesis Firehose)
# -----------------------------------------------------------------------------

# Read Grafana Cloud credentials for Firehose access key derivation
data "aws_secretsmanager_secret_version" "grafana_cloud" {
  count     = local.observability_enabled ? 1 : 0
  secret_id = var.grafana_cloud_credentials_secret_arn
}

locals {
  # Firehose access key format: <prometheus_user_id>:<metrics_write_token>
  firehose_access_key = local.observability_enabled ? "${var.grafana_cloud_prometheus_username}:${jsondecode(data.aws_secretsmanager_secret_version.grafana_cloud[0].secret_string)["metrics_write_token"]}" : ""
}

# S3 bucket for failed Firehose deliveries
resource "aws_s3_bucket" "observability_backup" {
  count  = local.observability_enabled ? 1 : 0
  bucket = "${var.cluster_name}-observability-backup-${random_id.s3_suffix.hex}"
  tags   = var.tags
}

resource "aws_s3_bucket_server_side_encryption_configuration" "observability_backup" {
  count  = local.observability_enabled ? 1 : 0
  bucket = aws_s3_bucket.observability_backup[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "observability_backup" {
  count  = local.observability_enabled ? 1 : 0
  bucket = aws_s3_bucket.observability_backup[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "observability_backup" {
  count  = local.observability_enabled ? 1 : 0
  bucket = aws_s3_bucket.observability_backup[0].id

  rule {
    id     = "expire-failed-deliveries"
    status = "Enabled"

    expiration {
      days = var.observability_log_retention_days
    }
  }
}

# CloudWatch log group for Firehose delivery diagnostics
resource "aws_cloudwatch_log_group" "firehose" {
  count             = local.observability_enabled ? 1 : 0
  name              = "/aws/firehose/${var.cluster_name}-grafana-metrics"
  retention_in_days = var.observability_log_retention_days
  tags              = var.tags
}

resource "aws_cloudwatch_log_stream" "firehose" {
  count          = local.observability_enabled ? 1 : 0
  name           = "HttpEndpointDelivery"
  log_group_name = aws_cloudwatch_log_group.firehose[0].name
}

# Kinesis Firehose Delivery Stream -> Grafana Cloud
resource "aws_kinesis_firehose_delivery_stream" "grafana_metrics" {
  count       = local.observability_enabled ? 1 : 0
  name        = "${var.cluster_name}-grafana-metrics"
  destination = "http_endpoint"
  depends_on  = [aws_iam_role_policy.firehose]

  http_endpoint_configuration {
    url                = var.grafana_cloud_firehose_endpoint
    name               = "Grafana Cloud"
    access_key         = local.firehose_access_key
    buffering_size     = 1
    buffering_interval = 60
    role_arn           = aws_iam_role.firehose[0].arn
    s3_backup_mode     = "FailedDataOnly"

    s3_configuration {
      role_arn           = aws_iam_role.firehose[0].arn
      bucket_arn         = aws_s3_bucket.observability_backup[0].arn
      buffering_size     = 5
      buffering_interval = 300
      compression_format = "GZIP"
    }

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose[0].name
      log_stream_name = aws_cloudwatch_log_stream.firehose[0].name
    }

    request_configuration {
      content_encoding = "GZIP"
    }
  }

  tags = var.tags
}

# CloudWatch Metric Stream
resource "aws_cloudwatch_metric_stream" "grafana" {
  count         = local.observability_enabled ? 1 : 0
  name          = "${var.cluster_name}-grafana"
  role_arn      = aws_iam_role.cw_metric_stream[0].arn
  firehose_arn  = aws_kinesis_firehose_delivery_stream.grafana_metrics[0].arn
  output_format = "opentelemetry1.0"
  depends_on    = [aws_iam_role_policy.cw_metric_stream]

  include_filter {
    namespace = "AWS/RDS"
  }

  include_filter {
    namespace = "AWS/ElastiCache"
  }

  include_filter {
    namespace = "AWS/ApplicationELB"
  }

  include_filter {
    namespace = "AWS/WAFV2"
  }

  include_filter {
    namespace = "AWS/S3"
  }

  tags = var.tags
}

# -----------------------------------------------------------------------------
# IAM: CloudWatch Metric Stream -> Firehose
# -----------------------------------------------------------------------------

resource "aws_iam_role" "cw_metric_stream" {
  count = local.observability_enabled ? 1 : 0
  name  = "${var.cluster_name}-cw-metric-stream-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "streams.metrics.cloudwatch.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = local.aws_account_id
          }
        }
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "cw_metric_stream" {
  count = local.observability_enabled ? 1 : 0
  name  = "${var.cluster_name}-cw-metric-stream-policy"
  role  = aws_iam_role.cw_metric_stream[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "firehose:PutRecord",
          "firehose:PutRecordBatch"
        ]
        Resource = aws_kinesis_firehose_delivery_stream.grafana_metrics[0].arn
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# IAM: Firehose -> S3 backup + CloudWatch Logs
# -----------------------------------------------------------------------------

resource "aws_iam_role" "firehose" {
  count = local.observability_enabled ? 1 : 0
  name  = "${var.cluster_name}-firehose-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "firehose.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = local.aws_account_id
          }
        }
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "firehose" {
  count = local.observability_enabled ? 1 : 0
  name  = "${var.cluster_name}-firehose-policy"
  role  = aws_iam_role.firehose[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:ListBucketMultipartUploads",
          "s3:PutObject"
        ]
        Resource = [
          aws_s3_bucket.observability_backup[0].arn,
          "${aws_s3_bucket.observability_backup[0].arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.firehose[0].arn}:*"
      }
    ]
  })
}
