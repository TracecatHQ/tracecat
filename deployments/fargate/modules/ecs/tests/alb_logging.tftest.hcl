mock_provider "aws" {}
mock_provider "random" {}
mock_provider "archive" {}
mock_provider "time" {}

variables {
  aws_region                     = "us-east-1"
  vpc_id                         = "vpc-0123456789abcdef0"
  public_subnet_ids              = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
  public_subnet_cidrs            = ["10.0.1.0/24", "10.0.2.0/24"]
  private_subnet_ids             = ["subnet-0123456789abcdef2", "subnet-0123456789abcdef3"]
  private_route_table_ids        = ["rtb-0123456789abcdef0"]
  domain_name                    = "app.example.com"
  hosted_zone_id                 = "Z0123456789EXAMPLE"
  acm_certificate_arn            = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
  tracecat_db_encryption_key_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:example-db-key"
  tracecat_service_key_arn       = "arn:aws:secretsmanager:us-east-1:123456789012:secret:example-service-key"
  tracecat_signing_secret_arn    = "arn:aws:secretsmanager:us-east-1:123456789012:secret:example-signing-key"
  user_auth_secret_arn           = "arn:aws:secretsmanager:us-east-1:123456789012:secret:example-auth-key"
}

run "logging_off_by_default" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  assert {
    condition     = length(aws_alb.this.access_logs) == 0 && length(aws_alb.this.connection_logs) == 0
    error_message = "ALB logging must be off when enable_alb_logging is omitted."
  }
}

run "bucket_alone_does_not_enable_logging" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "example-alb-logs"
  }

  assert {
    condition     = length(aws_alb.this.access_logs) == 0 && length(aws_alb.this.connection_logs) == 0
    error_message = "Providing an archive bucket must not implicitly enable ALB logging."
  }
}

run "logging_enabled_explicitly" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    enable_alb_logging  = true
    alb_log_bucket_name = "example-alb-logs"
  }

  assert {
    condition = (
      aws_alb.this.access_logs[0].enabled &&
      aws_alb.this.access_logs[0].bucket == "example-alb-logs" &&
      aws_alb.this.access_logs[0].prefix == "alb/access" &&
      aws_alb.this.connection_logs[0].enabled &&
      aws_alb.this.connection_logs[0].bucket == "example-alb-logs" &&
      aws_alb.this.connection_logs[0].prefix == "alb/connection"
    )
    error_message = "Explicit enablement must configure both streams and their separate prefixes."
  }
}

run "logging_explicitly_disabled" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    enable_alb_logging  = false
    alb_log_bucket_name = "example-alb-logs"
  }

  assert {
    condition     = length(aws_alb.this.access_logs) == 0 && length(aws_alb.this.connection_logs) == 0
    error_message = "Turning the switch off must remove both configured logging streams."
  }
}

run "enabled_logging_requires_destination" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    enable_alb_logging = true
  }

  expect_failures = [aws_alb.this]
}

run "invalid_bucket_is_rejected" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "INVALID_BUCKET"
  }

  expect_failures = [var.alb_log_bucket_name]
}
