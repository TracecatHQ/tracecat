# ECS Task Definition for API Service
resource "aws_ecs_task_definition" "api_task_definition" {
  family                   = "TracecatApiTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.api_execution.arn
  task_role_arn            = aws_iam_role.api_worker_task.arn

  container_definitions = jsonencode([
    {
      name  = "TracecatApiContainer"
      image = "${var.tracecat_image}:${local.tracecat_image_tag}"
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
      environment = local.api_env
      secrets     = local.tracecat_secrets
      dockerPullConfig = {
        maxAttempts = 3
        backoffTime = 10
      }
    }
  ])

  depends_on = [
    aws_ecs_service.temporal_service
  ]
}

resource "aws_ecs_service" "tracecat_api" {
  name                 = "tracecat-api"
  cluster              = aws_ecs_cluster.tracecat_cluster.id
  task_definition      = aws_ecs_task_definition.api_task_definition.arn
  launch_type          = "FARGATE"
  desired_count        = 1
  force_new_deployment = var.force_new_deployment

  network_configuration {
    subnets = var.private_subnet_ids
    security_groups = [
      aws_security_group.core.id,
      aws_security_group.core_db.id,
    ]
  }

  service_connect_configuration {
    enabled   = true
    namespace = local.local_dns_namespace
    service {
      port_name      = "api"
      discovery_name = "api-service"
      timeout {
        per_request_timeout_seconds = 120
      }
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
    aws_ecs_service.temporal_service,
  ]

}
