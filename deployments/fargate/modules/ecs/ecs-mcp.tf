# ECS Task Definition for MCP Service
resource "aws_ecs_task_definition" "mcp_task_definition" {
  count                    = var.enable_mcp ? 1 : 0
  family                   = "TracecatMcpTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.mcp_cpu
  memory                   = var.mcp_memory
  execution_role_arn       = aws_iam_role.mcp_execution[0].arn
  task_role_arn            = aws_iam_role.mcp_task[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "TracecatMcpContainer"
      image     = "${var.tracecat_image}:${local.tracecat_image_tag}"
      essential = true
      portMappings = [
        {
          containerPort = 8099
          hostPort      = 8099
          name          = "mcp"
          appProtocol   = "http"
        }
      ]
      command = ["python", "-m", "tracecat.mcp"]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "mcp"
        }
      }
      environment = local.mcp_env
      secrets     = local.mcp_secrets
      healthCheck = {
        command     = ["CMD", "curl", "-f", "http://localhost:8099/.well-known/oauth-authorization-server"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    }
  ])
}

resource "aws_ecs_service" "tracecat_mcp" {
  count                = var.enable_mcp ? 1 : 0
  name                 = "tracecat-mcp"
  cluster              = aws_ecs_cluster.tracecat_cluster.id
  task_definition      = aws_ecs_task_definition.mcp_task_definition[0].arn
  desired_count        = var.mcp_desired_count
  force_new_deployment = var.force_new_deployment

  network_configuration {
    subnets = var.private_subnet_ids
    security_groups = [
      aws_security_group.core.id,
      aws_security_group.caddy.id,
      aws_security_group.core_db.id,
      aws_security_group.redis.id,
    ]
  }

  service_connect_configuration {
    enabled   = true
    namespace = local.local_dns_namespace
    service {
      port_name      = "mcp"
      discovery_name = "mcp-service"
      timeout {
        per_request_timeout_seconds = 300
      }
      client_alias {
        port     = 8099
        dns_name = "mcp-service"
      }
    }

    log_configuration {
      log_driver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "service-connect-mcp"
      }
    }
  }

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }

  depends_on = [
    aws_ecs_service.tracecat_api
  ]
}
