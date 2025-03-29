# ECS Task Definition for Caddy Service
resource "aws_ecs_task_definition" "caddy_task_definition" {
  family                   = "TracecatCaddyTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.caddy_cpu
  memory                   = var.caddy_memory
  execution_role_arn       = aws_iam_role.caddy_execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name  = "TracecatCaddyContainer"
      image = "caddy:2.8.4-alpine"
      portMappings = [
        {
          containerPort = 80
          hostPort      = 80
          name          = "caddy"
          appProtocol   = "http"
        }
      ]
      command = [
        "/bin/sh",
        "-c",
        <<EOT
# Write the username and password hash to files for Caddy to use
if [ "${var.enable_metrics}" = "true" ]; then
  echo "Setting up metrics authentication" >&2
  echo "${var.metrics_auth_username}" > /etc/caddy/metrics_username
  echo "${var.metrics_auth_password_hash}" > /etc/caddy/metrics_password_hash
fi

# Create the Caddyfile with conditional metrics section
echo "Creating Caddyfile" >&2
cat > /etc/caddy/Caddyfile <<EOF
:80 {
  handle_path /api* {
    reverse_proxy http://api-service:8000
  }
  handle_path /temporal-admin* {
    reverse_proxy http://temporal-ui-service:8080
  }
EOF

# Conditionally add metrics section with basic auth
if [ "${var.enable_metrics}" = "true" ]; then
  cat >> /etc/caddy/Caddyfile <<EOF
  handle_path /metrics* {
    basicauth {
      \$(cat /etc/caddy/metrics_username) \$(cat /etc/caddy/metrics_password_hash)
    }
    reverse_proxy http://metrics-service:9000
  }
EOF
fi

# Complete the Caddyfile
cat >> /etc/caddy/Caddyfile <<EOF
  reverse_proxy http://ui-service:3000
}
EOF

echo "Caddyfile created:" >&2
cat /etc/caddy/Caddyfile >&2
echo "Starting Caddy" >&2
caddy run --config /etc/caddy/Caddyfile
EOT
      ]

      environment = var.enable_metrics ? [
        {
          name  = "METRICS_AUTH_USERNAME"
          value = var.metrics_auth_username
        },
        {
          name  = "METRICS_AUTH_PASSWORD_HASH"
          value = var.metrics_auth_password_hash
        }
      ] : []

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "caddy"
        }
      }
      dockerPullConfig = {
        maxAttempts = 3
        backoffTime = 10
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
    subnets = var.private_subnet_ids
    security_groups = [
      aws_security_group.caddy.id
    ]
  }

  service_connect_configuration {
    enabled   = true
    namespace = local.local_dns_namespace

    service {
      port_name      = "caddy"
      discovery_name = "caddy-service"
      client_alias {
        port     = 80
        dns_name = "caddy-service"
      }
    }

    log_configuration {
      log_driver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "service-connect-caddy"
      }
    }
  }

  load_balancer {
    target_group_arn = aws_alb_target_group.caddy.id
    container_name   = "TracecatCaddyContainer"
    container_port   = 80
  }

  depends_on = [
    aws_ecs_service.tracecat_api,
    aws_ecs_service.tracecat_ui,
    aws_ecs_service.tracecat_worker
  ]
}
