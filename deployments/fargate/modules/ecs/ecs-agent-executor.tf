# ECS Task Definition for Agent executor service
resource "aws_ecs_task_definition" "agent_executor_task_definition" {
  family                   = "TracecatAgentExecutorTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.agent_executor_cpu
  memory                   = var.agent_executor_memory
  execution_role_arn       = aws_iam_role.worker_execution.arn
  task_role_arn            = aws_iam_role.api_worker_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name    = "TracecatAgentExecutorContainer"
      image   = "${var.tracecat_image}:${local.tracecat_image_tag}"
      command = ["python", "-m", "tracecat.agent.worker"]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "agent-executor"
        }
      }
      environment = local.agent_executor_env
      secrets     = local.executor_secrets
      dockerPullConfig = {
        maxAttempts = 3
        backoffTime = 10
      }
    }
  ])
}

resource "aws_ecs_service" "tracecat_agent_executor" {
  name                 = "tracecat-agent-executor"
  cluster              = aws_ecs_cluster.tracecat_cluster.id
  task_definition      = aws_ecs_task_definition.agent_executor_task_definition.arn
  launch_type          = "FARGATE"
  desired_count        = var.agent_executor_desired_count
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
        awslogs-stream-prefix = "service-connect-agent-executor"
      }
    }
  }

  depends_on = [
    aws_ecs_service.temporal_service,
    aws_ecs_service.tracecat_api
  ]
}
