# ECS Task Definition for LiteLLM service
resource "aws_ecs_task_definition" "litellm_task_definition" {
  family                   = "TracecatLiteLLMTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.litellm_cpu
  memory                   = var.litellm_memory
  execution_role_arn       = aws_iam_role.worker_execution.arn
  task_role_arn            = aws_iam_role.executor_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name    = "TracecatLiteLLMContainer"
      image   = "${var.tracecat_image}:${local.tracecat_image_tag}"
      command = ["python", "-m", "tracecat.agent.litellm"]
      portMappings = [
        {
          containerPort = 4000
          hostPort      = 4000
          name          = "http"
          appProtocol   = "http"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "litellm"
        }
      }
      environment = local.litellm_env
      secrets     = local.tracecat_base_secrets
    }
  ])
}

resource "aws_ecs_service" "tracecat_litellm" {
  name                 = "tracecat-litellm"
  cluster              = aws_ecs_cluster.tracecat_cluster.id
  task_definition      = aws_ecs_task_definition.litellm_task_definition.arn
  launch_type          = "FARGATE"
  desired_count        = var.litellm_desired_count
  force_new_deployment = var.force_new_deployment

  network_configuration {
    subnets = var.private_subnet_ids
    security_groups = [
      aws_security_group.core.id,
      aws_security_group.core_db.id,
      aws_security_group.redis.id
    ]
  }

  service_connect_configuration {
    enabled   = true
    namespace = local.local_dns_namespace

    service {
      port_name      = "http"
      discovery_name = "litellm-service"
      timeout {
        per_request_timeout_seconds = 3600
        idle_timeout_seconds        = 3600
      }
      client_alias {
        port     = 4000
        dns_name = "litellm-service"
      }
    }

    log_configuration {
      log_driver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "service-connect-litellm"
      }
    }
  }

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 1
  }

  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 2
    base              = 0
  }

  depends_on = [
    aws_ecs_service.temporal_service,
    aws_ecs_service.tracecat_api
  ]
}
