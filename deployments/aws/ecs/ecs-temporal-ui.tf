# ECS Task Definition for Temporal UI Service
resource "aws_ecs_task_definition" "temporal_ui_task_definition" {
  count = var.disable_temporal_ui ? 0 : 1

  family                   = "TemporalUiTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.temporal_ui_execution.arn
  task_role_arn            = aws_iam_role.temporal_ui_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

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
      # https://github.com/temporalio/ui-server/tree/main/docker#quickstart-for-production
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
          value = "true"
        },
        {
          name  = "TEMPORAL_AUTH_SCOPES"
          value = "openid,profile,email"
        },
        {
          name  = "TEMPORAL_UI_PUBLIC_PATH"
          value = "/temporal-admin"
        },
        {
          name  = "TEMPORAL_AUTH_CALLBACK_URL"
          value = "${local.public_app_url}/temporal-admin/auth/sso/callback"
        },
        {
          name  = "TEMPORAL_AUTH_PROVIDER_URL"
          value = var.temporal_auth_provider_url
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
  count                = var.disable_temporal_ui ? 0 : 1
  name                 = "temporal-ui"
  cluster              = aws_ecs_cluster.tracecat_cluster.id
  task_definition      = aws_ecs_task_definition.temporal_ui_task_definition[count.index].arn
  launch_type          = "FARGATE"
  desired_count        = 1
  force_new_deployment = var.force_new_deployment

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
