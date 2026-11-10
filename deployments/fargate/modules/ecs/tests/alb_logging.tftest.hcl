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

run "reject_adjacent_dots" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "example..alb-logs"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_ipv4_address" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "192.0.2.1"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_reserved_xn_prefix" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "xn--example-logs"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_reserved_sthree_prefix" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "sthree-example-logs"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_reserved_demo_prefix" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "amzn-s3-demo-example-logs"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_reserved_s3alias_suffix" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "example-s3alias"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_reserved_ol_s3_suffix" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "example--ol-s3"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_reserved_mrap_suffix" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "example.mrap"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_reserved_x_s3_suffix" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "example--x-s3"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_reserved_table_s3_suffix" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "example--table-s3"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_too_short" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "ab"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_too_long" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_leading_dot" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = ".example-alb-logs"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "reject_trailing_hyphen" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    alb_log_bucket_name = "example-alb-logs-"
  }

  expect_failures = [var.alb_log_bucket_name]
}

run "accept_dotted_name" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    enable_alb_logging  = true
    alb_log_bucket_name = "example.alb-logs"
  }

  assert {
    condition = (
      aws_alb.this.access_logs[0].bucket == "example.alb-logs" &&
      aws_alb.this.connection_logs[0].bucket == "example.alb-logs"
    )
    error_message = "Valid S3 destination names must remain usable for both ALB log streams."
  }
}

run "accept_numeric_dotted_non_ip" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    enable_alb_logging  = true
    alb_log_bucket_name = "999.999.999.999"
  }

  assert {
    condition = (
      aws_alb.this.access_logs[0].bucket == "999.999.999.999" &&
      aws_alb.this.connection_logs[0].bucket == "999.999.999.999"
    )
    error_message = "Valid S3 destination names must remain usable for both ALB log streams."
  }
}

run "accept_incomplete_numeric_dotted_name" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    enable_alb_logging  = true
    alb_log_bucket_name = "1.2.3"
  }

  assert {
    condition = (
      aws_alb.this.access_logs[0].bucket == "1.2.3" &&
      aws_alb.this.connection_logs[0].bucket == "1.2.3"
    )
    error_message = "Valid S3 destination names must remain usable for both ALB log streams."
  }
}

run "accept_account_regional_namespace" {
  command = plan

  plan_options {
    target = [aws_alb.this]
  }

  variables {
    enable_alb_logging  = true
    alb_log_bucket_name = "example-123456789012-us-east-1-an"
  }

  assert {
    condition = (
      aws_alb.this.access_logs[0].bucket == "example-123456789012-us-east-1-an" &&
      aws_alb.this.connection_logs[0].bucket == "example-123456789012-us-east-1-an"
    )
    error_message = "Valid S3 destination names must remain usable for both ALB log streams."
  }
}
