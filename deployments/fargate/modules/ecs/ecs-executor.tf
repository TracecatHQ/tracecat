# ECS Task Definition for Executor service
resource "aws_ecs_task_definition" "executor_task_definition" {
  family                   = "${var.iam_name_prefix}ExecutorTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.executor_cpu
  memory                   = var.executor_memory
  execution_role_arn       = aws_iam_role.worker_execution.arn
  task_role_arn            = aws_iam_role.executor_task.arn

  volume {
    name = "registry-cache"
  }

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name    = "TracecatExecutorContainer"
      image   = "${var.tracecat_image}:${local.tracecat_image_tag}"
      command = ["python", "-m", "tracecat.executor.worker"]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "executor"
        }
      }
      essential = true
      dependsOn = [{ containerName = "RegistryCacheManager", condition = "HEALTHY" }]
      mountPoints = [{
        sourceVolume  = "registry-cache"
        containerPath = "/tmp/tracecat/registry-cache"
        readOnly      = true
      }]
      environment = concat(local.executor_env, [{
        name = "TRACECAT__EXECUTOR_REGISTRY_CACHE_REMOTE", value = "true"
      }])
      secrets = local.executor_secrets
    },
    {
      name      = "RegistryCacheManager"
      image     = "${var.tracecat_image}:${local.tracecat_image_tag}"
      user      = "0"
      essential = true
      command   = ["python", "-m", "tracecat.executor.registry_cache_manager"]
      # No container restart policy: a manager failure replaces the whole task.
      mountPoints = [{
        sourceVolume  = "registry-cache"
        containerPath = "/tmp/tracecat/registry-cache"
        readOnly      = false
      }]
      environment = [for name, value in {
        TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY        = aws_s3_bucket.registry.bucket
        TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES = var.executor_registry_cache_max_entries
        TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES   = var.executor_registry_cache_max_bytes
      } : { name = name, value = tostring(value) }]
      healthCheck = {
        command     = ["CMD", "python", "-m", "tracecat.executor.registry_cache_manager", "--health"]
        interval    = 10
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "registry-cache"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "tracecat_executor" {
  name                 = "tracecat-executor"
  cluster              = aws_ecs_cluster.tracecat_cluster.id
  task_definition      = aws_ecs_task_definition.executor_task_definition.arn
  launch_type          = "FARGATE"
  desired_count        = var.executor_desired_count
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
        awslogs-stream-prefix = "service-connect-executor"
      }
    }
  }

  depends_on = [
    aws_ecs_service.temporal_service,
    aws_ecs_service.tracecat_api
  ]
}
