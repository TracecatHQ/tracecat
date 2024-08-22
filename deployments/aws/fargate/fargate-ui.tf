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
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name  = "TracecatUiContainer"
      image = "${var.tracecat_ui_image}:${var.tracecat_ui_image_tag}"
      portMappings = [
        {
          containerPort = var.app_port 
          hostPort      = var.app_port 
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
      environment = local.tracecat_ui_environment 
      secrets = local.tracecat_ui_secrets 
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
      aws_security_group.ecs_tasks.id
    ]
    assign_public_ip = true
  }

  load_balancer {
        target_group_arn = aws_alb_target_group.tracecat_ui.id
        container_name  = "TracecatUiContainer"
        container_port   = var.app_port
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.namespace.arn
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

/*resource "aws_cloudwatch_log_group" "tracecat_log_group" {
  name              = "/ecs/tracecat"
  retention_in_days = 30
}*/

/*Service Discovery Namespace
resource "aws_service_discovery_http_namespace" "namespace" {
  name        = "tracecat-namespace"
  description = "Namespace for Tracecat services"
}*/
