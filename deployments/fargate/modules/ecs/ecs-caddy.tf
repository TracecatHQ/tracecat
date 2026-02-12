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
      image = "caddy:2.10.2-alpine"
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
# Write base Caddyfile
cat > /etc/caddy/Caddyfile <<'BASECONFIG'
:80 {
  handle_path /api* {
    header {
      Cache-Control "no-cache, no-transform"
      Pragma "no-cache"
      X-Accel-Buffering "no"
    }
    reverse_proxy http://api-service:8000 {
      flush_interval -1
      header_up Cache-Control "no-cache, no-transform"
      header_up Pragma "no-cache"
      header_up X-Accel-Buffering "no"
      header_up Connection "keep-alive"
      header_up Keep-Alive "timeout=300"
      header_down Connection "keep-alive"
      header_down Keep-Alive "timeout=300"
      header_down -Content-Length
      header_up X-Forwarded-For {remote_host}
      header_up X-Real-IP {remote_host}
      header_up X-Forwarded-Proto {scheme}
      header_up X-Forwarded-Host {host}
    }
  }

  handle /vercel* {
    header {
      Cache-Control "no-cache, no-transform"
      Pragma "no-cache"
      X-Accel-Buffering "no"
    }
    reverse_proxy http://api-service:8000 {
      flush_interval -1
      header_up Cache-Control "no-cache, no-transform"
      header_up Pragma "no-cache"
      header_up X-Accel-Buffering "no"
      header_up Connection "keep-alive"
      header_up Keep-Alive "timeout=300"
      header_down Connection "keep-alive"
      header_down Keep-Alive "timeout=300"
      header_down -Content-Length
      header_down Content-Type "text/event-stream"
      header_up X-Forwarded-For {remote_host}
      header_up X-Real-IP {remote_host}
      header_up X-Forwarded-Proto {scheme}
      header_up X-Forwarded-Host {host}
    }
  }
  handle_path /temporal-admin* {
    reverse_proxy http://temporal-ui-service:8080
  }
BASECONFIG

# Conditionally append metrics config
if [ "${var.enable_metrics}" = "true" ]; then
  cat >> /etc/caddy/Caddyfile <<'METRICSCONFIG'
  handle_path /metrics* {
    basic_auth {
      {$METRICS_AUTH_USERNAME} {$METRICS_AUTH_PASSWORD_HASH}
    }
    reverse_proxy http://metrics-service:9000
  }
METRICSCONFIG
fi

# Append final UI config
cat >> /etc/caddy/Caddyfile <<'UICONFIG'
  handle_path /vercel* {
    header {
      Cache-Control "no-cache, no-transform"
      Pragma "no-cache"
      X-Accel-Buffering "no"
    }
    reverse_proxy http://ui-service:3000 {
      flush_interval 0s
      header_up Cache-Control "no-cache, no-transform"
      header_up Pragma "no-cache"
      header_up X-Accel-Buffering "no"
    }
  }

  reverse_proxy http://ui-service:3000
}
UICONFIG

# Run Caddy
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
      timeout {
        per_request_timeout_seconds = 300
      }
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
