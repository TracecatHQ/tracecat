# ECS Task Definition for Temporal Service
resource "aws_ecs_task_definition" "temporal_task_definition" {
  family                   = "TracecatTemporalTaskDefinition"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.temporal_cpu
  memory                   = var.temporal_memory
  execution_role_arn       = aws_iam_role.temporal_execution.arn
  task_role_arn            = aws_iam_role.temporal_task.arn

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
      environment = concat(local.temporal_env, [
        {
          name  = "POSTGRES_SEEDS"
          value = local.temp_db_hostname
        }
      ])
      secrets = local.temporal_secrets

      dockerPullConfig = {
        maxAttempts = 3
        backoffTime = 30
      }

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
    subnets = aws_subnet.private[*].id
    security_groups = [
      aws_security_group.core.id,
      aws_security_group.temporal_db.id
    ]
  }

  service_connect_configuration {
    enabled   = true
    namespace = local.local_dns_namespace
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

# Lambda function to restart Temporal service on password rotation
resource "aws_lambda_function" "restart_temporal_service" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "restart_temporal_service"
  role             = aws_iam_role.temporal_service_restart_lambda_exec.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.9"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      ECS_CLUSTER = aws_ecs_cluster.tracecat_cluster.name
      ECS_SERVICE = aws_ecs_service.temporal_service.name
    }
  }

  depends_on = [data.archive_file.lambda_zip]
}

data "archive_file" "lambda_zip" {
  type        = "zip"
  output_path = "${path.module}/lambda_function.zip"

  source {
    content  = <<EOF
import os
import boto3

def lambda_handler(event, context):
    ecs = boto3.client('ecs')

    cluster = os.environ['ECS_CLUSTER']
    service = os.environ['ECS_SERVICE']

    try:
        response = ecs.update_service(
            cluster=cluster,
            service=service,
            forceNewDeployment=True
        )
        print(f"Successfully initiated restart of ECS service {service}")
        return {
            'statusCode': 200,
            'body': 'Service restart initiated'
        }
    except Exception as e:
        print(f"Error restarting ECS service: {str(e)}")
        return {
            'statusCode': 500,
            'body': 'Error restarting service'
        }
EOF
    filename = "lambda_function.py"
  }
}

resource "aws_iam_role" "temporal_service_restart_lambda_exec" {
  name = "temporal_service_restart_lambda_exec_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "temporal_service_restart_lambda_exec_policy" {
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  role       = aws_iam_role.temporal_service_restart_lambda_exec.name
}

resource "aws_iam_role_policy" "temporal_service_restart_policy" {
  name = "temporal_service_restart_policy"
  role = aws_iam_role.temporal_service_restart_lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecs:UpdateService",
          "ecs:DescribeServices"
        ]
        Resource = "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:service/${aws_ecs_cluster.tracecat_cluster.name}/${aws_ecs_service.temporal_service.name}"
      }
    ]
  })
}

resource "aws_cloudwatch_event_rule" "temporal_db_password_rotation" {
  name        = "temporal_db_password_rotation_rule"
  description = "Capture Temporal DB password rotation events"

  event_pattern = jsonencode({
    source      = ["aws.secretsmanager"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["secretsmanager.amazonaws.com"]
      eventName   = ["RotateSecret"]
      requestParameters = {
        secretId = [data.aws_secretsmanager_secret.temporal_db_password.arn]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "temporal_service_restart_lambda" {
  rule      = aws_cloudwatch_event_rule.temporal_db_password_rotation.name
  target_id = "InvokeTemporalServiceRestartLambda"
  arn       = aws_lambda_function.restart_temporal_service.arn
}

resource "aws_lambda_permission" "allow_cloudwatch_to_call_restart_temporal_service" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.restart_temporal_service.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.temporal_db_password_rotation.arn
}
