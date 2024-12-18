# ECS Task Definition for Worker Service
resource "aws_ecs_task_definition" "worker_task_definition" {
  family                   = "TracecatWorkerTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.worker_execution.arn
  task_role_arn            = aws_iam_role.api_worker_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name    = "TracecatWorkerContainer"
      image   = "${var.tracecat_image}:${local.tracecat_image_tag}"
      command = ["python", "tracecat/dsl/worker.py"]
      portMappings = [
        {
          containerPort = 8001
          hostPort      = 8001
          name          = "worker"
          appProtocol   = "http"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "worker"
        }
      }
      environment = local.worker_env
      secrets     = local.tracecat_base_secrets
      dockerPullConfig = {
        maxAttempts = 3
        backoffTime = 30
      }
    }
  ])
}

resource "aws_ecs_service" "tracecat_worker" {
  name                 = "tracecat-worker"
  cluster              = aws_ecs_cluster.tracecat_cluster.id
  task_definition      = aws_ecs_task_definition.worker_task_definition.arn
  launch_type          = "FARGATE"
  desired_count        = 2
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
      port_name      = "worker"
      discovery_name = "worker-service"
      timeout {
        per_request_timeout_seconds = 120
      }
      client_alias {
        port     = 8001
        dns_name = "worker-service"
      }
    }

    log_configuration {
      log_driver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "service-connect-worker"
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
    aws_ecs_service.tracecat_executor
  ]
}
