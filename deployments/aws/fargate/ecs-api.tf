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
      environment = concat(local.api_env, [
        {
          name  = "TRACECAT__DB_ENDPOINT"
          value = local.core_db_hostname
        }
      ])
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
      aws_security_group.core.id,
      aws_security_group.core_db.id,
    ]
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
