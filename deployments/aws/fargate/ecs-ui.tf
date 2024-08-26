# UI Service resources for Tracecat
variable "tracecat_ui_image" {
  default = "ghcr.io/tracecathq/tracecat-ui"
}

variable "tracecat_ui_image_tag" {
  #default = "latest"
  default = "0.5.2"
}

# ECS Task Definition for UI Service
resource "aws_ecs_task_definition" "ui_task_definition" {
  family                   = "TracecatUiTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ui_execution.arn

  container_definitions = jsonencode([
    {
      name  = "TracecatUiContainer"
      image = "${var.tracecat_ui_image}:${var.tracecat_ui_image_tag}"
      portMappings = [
        {
          containerPort = 3000 
          hostPort      = 3000 
          name          = "ui"
          appProtocol   = "http"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region 
          awslogs-stream-prefix = "ui"
        }
      }
      environment = local.ui_env 
    }
  ])
}

resource "aws_ecs_service" "tracecat_ui" {
  name            = "tracecat-ui"
  cluster         = aws_ecs_cluster.tracecat_cluster.id
  task_definition = aws_ecs_task_definition.ui_task_definition.arn
  launch_type     = "FARGATE"
  desired_count   = 1

  network_configuration {
    subnets         = aws_subnet.private.*.id
    security_groups = [
      aws_security_group.core.id,
    ]
  }

  service_connect_configuration {
    enabled   = true
    namespace = local.local_dns_namespace
    service {
      port_name      = "ui"
      discovery_name = "ui-service"
      client_alias {
        port     = 3000 
        dns_name = "ui-service"
      }
    }

    log_configuration {
      log_driver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
        awslogs-region        = var.aws_region 
        awslogs-stream-prefix = "service-connect-ui"
      }
    }
  }

  depends_on = [
    aws_ecs_service.tracecat_api
  ]
}
