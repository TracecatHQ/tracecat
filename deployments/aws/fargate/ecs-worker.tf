# ECS Task Definition for Worker Service
resource "aws_ecs_task_definition" "worker_task_definition" {
  family                   = "TracecatWorkerTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  #cpu                      = "256"
  cpu                      = "1024"
  #memory                   = "512"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name  = "TracecatWorkerContainer"
      image = "${var.tracecat_image_api}:${var.tracecat_image_api_tag}"
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
      environment = concat(local.api_env, [
        {
          name  = "TRACECAT__DB_ENDPOINT"
          value = local.core_db_hostname
        }
      ])
      secrets = local.tracecat_secrets
    }
  ])

  depends_on = [
    aws_ecs_service.temporal_service,
    aws_ecs_task_definition.temporal_task_definition,
  ]
}

resource "aws_ecs_service" "tracecat_worker" {
  name            = "tracecat-worker"
  cluster         = aws_ecs_cluster.tracecat_cluster.id
  task_definition = aws_ecs_task_definition.worker_task_definition.arn
  launch_type     = "FARGATE"
  desired_count   = 1

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [
      aws_security_group.core.id,
      aws_security_group.core_db.id,
    ]
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.namespace.arn
    service {
      port_name      = "worker"
      discovery_name = "worker-service"
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

  depends_on = [
    aws_ecs_service.temporal_service,
  ]
}
