# ECS Task Definition for Executor Service
resource "aws_ecs_task_definition" "executor_task_definition" {
  family                   = "TracecatExecutorTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.executor_cpu
  memory                   = var.executor_memory
  execution_role_arn       = aws_iam_role.worker_execution.arn
  task_role_arn            = aws_iam_role.api_worker_task.arn

  container_definitions = jsonencode([
    {
      name  = "TracecatExecutorContainer"
      image = "${var.tracecat_image}:${local.tracecat_image_tag}"
      command = [
        "python",
        "-m",
        "uvicorn",
        "tracecat.api.executor:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000"
      ]
      portMappings = [
        {
          containerPort = 8002
          hostPort      = 8002
          name          = "executor"
          appProtocol   = "http"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "executor"
        }
      }
      environment = local.executor_env
      secrets     = local.tracecat_base_secrets
      dockerPullConfig = {
        maxAttempts = 3
        backoffTime = 10
      }
    }
  ])
}

resource "aws_ecs_service" "tracecat_executor" {
  name                 = "tracecat-executor"
  cluster              = aws_ecs_cluster.tracecat_cluster.id
  task_definition      = aws_ecs_task_definition.executor_task_definition.arn
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
      port_name      = "executor"
      discovery_name = "executor-service"
      timeout {
        per_request_timeout_seconds = 120
      }
      client_alias {
        port     = 8002
        dns_name = "executor-service"
      }
    }

    log_configuration {
      log_driver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "service-connect-executor"
      }
    }
  }
}
