# ECS Task Definition for LiteLLM Service
resource "aws_ecs_task_definition" "litellm_task_definition" {
  family                   = "${var.iam_name_prefix}LitellmTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.litellm_cpu
  memory                   = var.litellm_memory
  execution_role_arn       = aws_iam_role.litellm_execution.arn
  task_role_arn            = aws_iam_role.litellm_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "TracecatLitellmContainer"
      image     = "${var.tracecat_image}:${local.tracecat_image_tag}"
      essential = true
      # k8s lets LiteLLM drain for 30 min; Fargate caps stopTimeout at 120 s.
      stopTimeout = local.litellm_stop_timeout_seconds
      portMappings = [
        {
          containerPort = 4000
          hostPort      = 4000
          name          = "litellm"
          appProtocol   = "http"
        }
      ]
      command = ["python", "-m", "tracecat.agent.litellm"]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "litellm"
        }
      }
      environment = local.litellm_env
      secrets     = local.litellm_secrets
      healthCheck = {
        command     = ["CMD", "curl", "-f", "http://localhost:4000/health/readiness"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    }
  ])
}

resource "aws_ecs_service" "tracecat_litellm" {
  name                 = "tracecat-litellm"
  cluster              = aws_ecs_cluster.tracecat_cluster.id
  task_definition      = aws_ecs_task_definition.litellm_task_definition.arn
  desired_count        = var.litellm_desired_count
  force_new_deployment = var.force_new_deployment

  # Start replacements before stopping old tasks (k8s maxUnavailable 0).
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

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
      port_name      = "litellm"
      discovery_name = "litellm-service"
      timeout {
        # Match the API service so LLM streams can last a full agent turn.
        per_request_timeout_seconds = max(3600, tonumber(var.agent_sandbox_timeout) + 60)
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

  depends_on = [
    aws_ecs_service.tracecat_api
  ]
}
