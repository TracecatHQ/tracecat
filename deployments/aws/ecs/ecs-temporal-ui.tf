# ECS Task Definition for Temporal UI Service
resource "aws_ecs_task_definition" "temporal_ui_task_definition" {
  family                   = "TemporalUiTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.temporal_ui_execution.arn
  task_role_arn            = aws_iam_role.temporal_ui_task.arn

  container_definitions = jsonencode([
    {
      name  = "TemporalUiContainer"
      image = "temporalio/ui:${var.temporal_ui_image_tag}"
      portMappings = [
        {
          containerPort = 8080
          hostPort      = 8080
          name          = "temporal-ui"
          appProtocol   = "http"
        }
      ]
      environment = [
        {
          name  = "TEMPORAL_ADDRESS"
          value = "temporal-service:7233"
        },
        {
          name  = "TEMPORAL_CORS_ORIGINS"
          value = "http://localhost:3000"
        },
        {
          name  = "TEMPORAL_AUTH_ENABLED"
          value = "false"
        },
        {
          name  = "TEMPORAL_AUTH_SCOPES"
          value = "openid,email,profile"
        }
      ]
      secrets = local.temporal_ui_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "temporal-ui"
        }
      }
      dockerPullConfig = {
        maxAttempts = 3
        backoffTime = 30
      }
    }
  ])
}

resource "aws_ecs_service" "temporal_ui_service" {
  name                   = "temporal-ui"
  cluster                = aws_ecs_cluster.tracecat_cluster.id
  task_definition        = aws_ecs_task_definition.temporal_ui_task_definition.arn
  launch_type            = "FARGATE"
  desired_count          = 1
  force_new_deployment   = var.force_new_deployment
  enable_execute_command = true

  network_configuration {
    subnets = var.private_subnet_ids
    security_groups = [
      aws_security_group.temporal.id,
      aws_security_group.caddy.id
    ]
  }

  service_connect_configuration {
    enabled   = true
    namespace = local.local_dns_namespace
    service {
      port_name      = "temporal-ui"
      discovery_name = "temporal-ui-service"
      client_alias {
        port     = 8080
        dns_name = "temporal-ui-service"
      }
    }

    log_configuration {
      log_driver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "service-connect-temporal-ui"
      }
    }
  }

  depends_on = [
    aws_ecs_service.temporal_service
  ]
}

# Execution role for Temporal UI
resource "aws_iam_role" "temporal_ui_execution" {
  name               = "TemporalUIExecutionRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

resource "aws_iam_role_policy_attachment" "temporal_ui_execution_cloudwatch_logs" {
  policy_arn = aws_iam_policy.cloudwatch_logs.arn
  role       = aws_iam_role.temporal_ui_execution.name
}

# Task role for Temporal UI
resource "aws_iam_role" "temporal_ui_task" {
  name               = "TemporalUITaskRole"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}
