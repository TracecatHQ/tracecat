# ECS Task Definition for Executor Service
resource "aws_ecs_task_definition" "executor_task_definition" {
  family                   = "TracecatExecutorTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.executor_cpu
  memory                   = var.executor_memory
  execution_role_arn       = aws_iam_role.worker_execution.arn
  task_role_arn            = aws_iam_role.api_worker_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

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
        "8002"
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
      healthCheck = {
        command     = ["CMD", "python", "-c", "import httpx; httpx.get('http://localhost:8002/').raise_for_status()"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
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

# Autoscaling for the executor service
resource "aws_appautoscaling_target" "executor_scaling_target" {
  max_capacity       = 4
  min_capacity       = 1
  resource_id        = "service/${aws_ecs_cluster.tracecat_cluster.name}/${aws_ecs_service.tracecat_executor.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"

  # Ensure the service-linked role is created before the scaling target
  depends_on = [aws_iam_service_linked_role.ecs_autoscaling]
}

# CPU-based autoscaling policy
resource "aws_appautoscaling_policy" "executor_cpu_scaling_policy" {
  name               = "executor-cpu-scaling-policy"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.executor_scaling_target.resource_id
  scalable_dimension = aws_appautoscaling_target.executor_scaling_target.scalable_dimension
  service_namespace  = aws_appautoscaling_target.executor_scaling_target.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 40.0 # Scale when CPU exceeds 40% (more aggressive scaling)
    scale_in_cooldown  = 120  # Wait 2 minutes before scaling in (balanced approach)
    scale_out_cooldown = 20   # Very rapid scale out (20 seconds) for bursty workloads
  }
}
