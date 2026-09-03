# CloudMap Namespace for Service Connect
resource "aws_service_discovery_http_namespace" "namespace" {
  name        = "${var.name_prefix}.local"
  description = "Private DNS namespace for ECS services"
}

resource "time_sleep" "wait_for_namespace" {
  depends_on = [aws_service_discovery_http_namespace.namespace]

  create_duration = "20s"
}

resource "aws_ecs_cluster" "tracecat_cluster" {
  name = "${var.name_prefix}-cluster"

  depends_on = [time_sleep.wait_for_namespace]

  service_connect_defaults {
    namespace = aws_service_discovery_http_namespace.namespace.arn
  }

  # Enable Container Insights
  setting {
    name  = "containerInsights"
    value = "enhanced"
  }
}

locals {
  local_dns_namespace = aws_ecs_cluster.tracecat_cluster.service_connect_defaults[0].namespace
}
