# API Service resources for Tracecat

variable "tracecat_image_api" {
  default = "ghcr.io/tracecathq/tracecat"
}

variable "tracecat_image_api_tag" {
  #default = "latest"
  default = "0.5.2"
}

variable "fargate_cpu" {
  default = "256"
} 

variable "fargate_memory" {
  default = "512"
} 

# ECS Task Definition for API Service
resource "aws_ecs_task_definition" "api_task_definition" {
  family                   = "TracecatApiTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.fargate_cpu 
  memory                   = var.fargate_memory 
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name  = "TracecatApiContainer"
      image = "${var.tracecat_image_api}:${var.tracecat_image_api_tag}"
      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          name          = "api"
          appProtocol   = "http"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region 
          awslogs-stream-prefix = "api"
        }
      }
      environment = local.tracecat_environment 
      secrets = local.tracecat_secrets 
    }
  ])

  depends_on = [
    aws_ecs_service.temporal_service
  ]
}

resource "aws_ecs_service" "tracecat_api" {
  name            = "tracecat-api"
  cluster         = aws_ecs_cluster.tracecat_cluster.id
  task_definition = aws_ecs_task_definition.api_task_definition.arn
  launch_type     = "FARGATE"
  desired_count   = 1

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [
      aws_security_group.ecs_tasks.id,
      aws_security_group.core_security.id,
      aws_security_group.temporal_security.id,
      aws_security_group.core_db_security.id
    ]
    assign_public_ip = true
  }

  load_balancer {
        target_group_arn = aws_alb_target_group.tracecat_api.id
        container_name  = "TracecatApiContainer"
        container_port  = "8000" 
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.namespace.arn
    service {
      port_name      = "api"
      discovery_name = "api-service"
      client_alias {
        port     = 8000
        dns_name = "api-service"
      }
    }

    log_configuration {
      log_driver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
        awslogs-region        = var.aws_region 
        awslogs-stream-prefix = "service-connect-api"
      }
    }
  }

  depends_on = [
    aws_ecs_service.temporal_service
  ]

}

resource "aws_cloudwatch_log_group" "tracecat_log_group" {
  name              = "/ecs/tracecat"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_stream" "tracecat_log_stream" {
  name           = "tc-log-stream"
  log_group_name = aws_cloudwatch_log_group.tracecat_log_group.name
}

# Service Discovery Namespace
resource "aws_service_discovery_http_namespace" "namespace" {
  name        = "tracecat-namespace"
  description = "Namespace for Tracecat services"
}