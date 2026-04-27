# ECS Task Definition for Agent worker service
resource "aws_ecs_task_definition" "agent_worker_task_definition" {
  family                   = "${var.iam_name_prefix}AgentWorkerTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.agent_worker_cpu
  memory                   = var.agent_worker_memory
  execution_role_arn       = aws_iam_role.worker_execution.arn
  task_role_arn            = aws_iam_role.api_worker_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name    = "TracecatAgentWorkerContainer"
      image   = "${var.tracecat_image}:${local.tracecat_image_tag}"
      command = ["python", "-m", "tracecat.agent.worker"]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "agent-worker"
        }
      }
      environment = local.agent_worker_env
      secrets     = local.tracecat_temporal_secrets
    }
  ])
}

resource "aws_ecs_service" "tracecat_agent_worker" {
  name                 = "tracecat-agent-worker"
  cluster              = aws_ecs_cluster.tracecat_cluster.id
  task_definition      = aws_ecs_task_definition.agent_worker_task_definition.arn
  desired_count        = var.agent_worker_desired_count
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

    log_configuration {
      log_driver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "service-connect-agent-worker"
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
    weight            = 1
    base              = 0
  }

  depends_on = [
    aws_ecs_service.temporal_service,
    aws_ecs_service.tracecat_api
  ]
}
