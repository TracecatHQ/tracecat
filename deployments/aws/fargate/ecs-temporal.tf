# ECS Task Definition for Temporal Service
resource "aws_ecs_task_definition" "temporal_task_definition" {
  family                   = "TracecatTemporalTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

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
      environment = local.temporal_environment 
      secrets = local.temporal_secrets 

      runtime_platform = {
        cpu_architecture        = "ARM64"
        operating_system_family = "LINUX"
      }
    }
  ])
}

resource "aws_ecs_service" "temporal_service" {
  name            = "temporal-server"
  cluster         = aws_ecs_cluster.tracecat_cluster.id
  task_definition = aws_ecs_task_definition.temporal_task_definition.arn
  launch_type     = "FARGATE"
  desired_count   = 1

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [
      aws_security_group.core_security.id
    ]
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.namespace.arn
    service {
      port_name      = "temporal"
      discovery_name = "temporal-service"
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

/*resource "aws_cloudwatch_log_group" "tracecat_log_group" {
  name              = "/ecs/tracecat"
  retention_in_days = 30
}*/

/*Service Discovery Namespace
resource "aws_service_discovery_http_namespace" "namespace" {
  name        = "tracecat-namespace"
  description = "Namespace for Tracecat services"
}*/
