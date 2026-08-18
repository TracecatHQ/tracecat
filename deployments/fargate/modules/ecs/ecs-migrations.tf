# ECS Task Definition for one-shot database migrations
resource "aws_ecs_task_definition" "migrations_task_definition" {
  family                   = "${var.iam_name_prefix}MigrationsTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.migrations_cpu
  memory                   = var.migrations_memory
  execution_role_arn       = aws_iam_role.migrations_execution.arn
  task_role_arn            = aws_iam_role.migrations_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = local.tracecat_migrations_container_name
      image     = "${local.tracecat_migrations_image}:${local.tracecat_migrations_image_tag}"
      essential = true
      command   = ["python3", "-m", "alembic", "upgrade", "head"]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tracecat_log_group.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "migrations"
        }
      }
      environment = local.migrations_env
    }
  ])
}
