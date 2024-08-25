# ECS Task Definition for Caddy Service
resource "aws_ecs_task_definition" "caddy_task_definition" {
  family                   = "TracecatCaddyTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name  = "TracecatCaddyContainer"
      image = "caddy:2.8.4-alpine"
      portMappings = [
        {
          containerPort = 80
          hostPort      = 80
          protocol      = "tcp"
        }
      ]
      command = [
        "/bin/sh",
        "-c",
        <<EOT
cat <<EOF > /etc/caddy/Caddyfile
:80 {
  handle_path /api* {
    reverse_proxy http://api-service:8000
  }
  reverse_proxy http://ui-service:3000
}
EOF
caddy run --config /etc/caddy/Caddyfile
EOT
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "caddy"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "tracecat_caddy" {
  name            = "tracecat-caddy"
  cluster         = aws_ecs_cluster.tracecat_cluster.id
  task_definition = aws_ecs_task_definition.caddy_task_definition.arn
  launch_type     = "FARGATE"
  desired_count   = 1

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  load_balancer {
    target_group_arn = aws_alb_target_group.tracecat_caddy.id
    container_name   = "TracecatCaddyContainer"
    container_port   = 80
  }
}
