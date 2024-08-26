# CloudMap Namespace for Service Connect
resource "aws_service_discovery_http_namespace" "namespace" {
  name        = "tracecat.local"
  description = "Private DNS namespace for ECS services"
}

resource "aws_ecs_cluster" "tracecat_cluster" {
  name = "tracecat-cluster"

  service_connect_defaults {
    namespace = aws_service_discovery_http_namespace.namespace.arn
  }
}

locals {
  local_dns_namespace = aws_ecs_cluster.tracecat_cluster.service_connect_defaults[0].namespace
}
