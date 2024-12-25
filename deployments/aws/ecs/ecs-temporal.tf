# ECS Task Definition for Temporal Service
resource "aws_ecs_task_definition" "temporal_task_definition" {
  count                    = var.disable_temporal_autosetup ? 0 : 1
  family                   = "TracecatTemporalTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.temporal_cpu
  memory                   = var.temporal_memory
  execution_role_arn       = aws_iam_role.temporal_execution.arn
  task_role_arn            = aws_iam_role.temporal_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name  = "TemporalContainer"
      image = "${var.temporal_server_image}:${var.temporal_server_image_tag}"
      portMappings = [
        {
          containerPort = 7233
          hostPort      = 7233
          name          = "temporal"
          appProtocol   = "grpc"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "temporal"
        }
      }
      environment = concat(local.temporal_env, [
        {
          name  = "POSTGRES_SEEDS"
          value = local.temp_db_hostname
        }
      ])
      secrets = local.temporal_secrets

      dockerPullConfig = {
        maxAttempts = 3
        backoffTime = 30
      }

      runtime_platform = {
        cpu_architecture        = "ARM64"
        operating_system_family = "LINUX"
      }
    }
  ])
}

resource "aws_ecs_service" "temporal_service" {
  count           = var.disable_temporal_autosetup ? 0 : 1
  name            = "temporal-server"
  cluster         = aws_ecs_cluster.tracecat_cluster.id
  task_definition = aws_ecs_task_definition.temporal_task_definition[0].arn
  launch_type     = "FARGATE"
  desired_count   = 1

  network_configuration {
    subnets = var.private_subnet_ids
    security_groups = [
      aws_security_group.core.id,
      aws_security_group.temporal.id,
      aws_security_group.temporal_db.id
    ]
  }

  service_connect_configuration {
    enabled   = true
    namespace = local.local_dns_namespace
    service {
      port_name      = "temporal"
      discovery_name = "temporal-service"
      timeout {
        per_request_timeout_seconds = 120
      }
      client_alias {
        port     = 7233
        dns_name = "temporal-service"
      }
    }

    log_configuration {
      log_driver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "service-connect-temporal"
      }
    }
  }

}
