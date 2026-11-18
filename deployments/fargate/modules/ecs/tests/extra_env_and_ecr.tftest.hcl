# The env references IAM roles, whose trust policies must be valid JSON.
mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}
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

run "extra_environment_reaches_backend_env" {
  command = plan

  plan_options {
    target = [aws_ecs_task_definition.api_task_definition]
  }

  variables {
    extra_environment = {
      TRACECAT__AGENT_RUNTIME_ENABLED = "false"
      LOG_LEVEL                       = "DEBUG"
    }
  }

  assert {
    condition     = local.tracecat_common_env["TRACECAT__AGENT_RUNTIME_ENABLED"] == "false"
    error_message = "extra_environment must be added to the common backend env."
  }

  assert {
    condition     = local.tracecat_common_env["LOG_LEVEL"] == "DEBUG"
    error_message = "extra_environment must override module-set values with the same name."
  }
}

run "s3_endpoint_allows_ecr_layers" {
  command = plan

  plan_options {
    target = [aws_vpc_endpoint.s3]
  }

  # Bucket ARNs are computed; fix them so the endpoint policy is known at plan.
  override_resource {
    target          = aws_s3_bucket.attachments
    override_during = plan
    values          = { arn = "arn:aws:s3:::attachments" }
  }
  override_resource {
    target          = aws_s3_bucket.registry
    override_during = plan
    values          = { arn = "arn:aws:s3:::registry" }
  }
  override_resource {
    target          = aws_s3_bucket.skills
    override_during = plan
    values          = { arn = "arn:aws:s3:::skills" }
  }
  override_resource {
    target          = aws_s3_bucket.workflow
    override_during = plan
    values          = { arn = "arn:aws:s3:::workflow" }
  }
  override_resource {
    target          = aws_s3_bucket.agent
    override_during = plan
    values          = { arn = "arn:aws:s3:::agent" }
  }

  assert {
    condition = anytrue([
      for s in jsondecode(aws_vpc_endpoint.s3.policy).Statement :
      contains(flatten([s.Resource]), "arn:aws:s3:::prod-us-east-1-starport-layer-bucket/*")
    ])
    error_message = "The S3 endpoint policy must let tasks fetch ECR image layers."
  }
}
