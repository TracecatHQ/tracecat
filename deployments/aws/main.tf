terraform {
  required_version = ">= 1.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
provider "aws" {
  region = var.aws_region
}

module "network" {
  source = "./network"

  aws_region     = var.aws_region
  domain_name    = var.domain_name
  hosted_zone_id = var.hosted_zone_id
}

module "ecs" {
  source = "./ecs"

  # Network configuration from network module
  vpc_id             = module.network.vpc_id
  public_subnet_ids  = module.network.public_subnet_ids
  private_subnet_ids = module.network.private_subnet_ids

  # Get certificate from ACM module
  acm_certificate_arn = module.network.acm_certificate_arn

  aws_region     = var.aws_region
  domain_name    = var.domain_name
  hosted_zone_id = var.hosted_zone_id

  # Tracecat version
  TFC_CONFIGURATION_VERSION_GIT_COMMIT_SHA = var.TFC_CONFIGURATION_VERSION_GIT_COMMIT_SHA
  tracecat_image_tag                       = var.tracecat_image_tag
  use_git_commit_sha                       = var.use_git_commit_sha
  force_new_deployment                     = var.force_new_deployment

  # Container environment variables
  tracecat_app_env   = var.tracecat_app_env
  log_level          = var.log_level
  temporal_log_level = var.temporal_log_level

  # RDS settings
  restore_from_snapshot       = var.restore_from_snapshot
  rds_backup_retention_period = var.rds_backup_retention_period

  # Custom integrations
  remote_repository_package_name = var.remote_repository_package_name
  remote_repository_url          = var.remote_repository_url

  # Secrets from AWS Secrets Manager
  tracecat_db_encryption_key_arn = var.tracecat_db_encryption_key_arn
  tracecat_service_key_arn       = var.tracecat_service_key_arn
  tracecat_signing_secret_arn    = var.tracecat_signing_secret_arn

  # Authentication
  auth_types           = var.auth_types
  auth_allowed_domains = var.auth_allowed_domains

  # OAuth
  oauth_client_id_arn     = var.oauth_client_id_arn
  oauth_client_secret_arn = var.oauth_client_secret_arn

  # SAML SSO
  saml_idp_entity_id_arn    = var.saml_idp_entity_id_arn
  saml_idp_redirect_url_arn = var.saml_idp_redirect_url_arn
  saml_idp_certificate_arn  = var.saml_idp_certificate_arn
  saml_idp_metadata_url_arn = var.saml_idp_metadata_url_arn

  # Temporal UI
  temporal_auth_provider_url      = var.temporal_auth_provider_url
  temporal_auth_client_id_arn     = var.temporal_auth_client_id_arn
  temporal_auth_client_secret_arn = var.temporal_auth_client_secret_arn
  disable_temporal_ui             = var.disable_temporal_ui

  # Compute / memory
  api_cpu                     = var.api_cpu
  api_memory                  = var.api_memory
  worker_cpu                  = var.worker_cpu
  worker_memory               = var.worker_memory
  executor_cpu                = var.executor_cpu
  executor_memory             = var.executor_memory
  executor_client_timeout     = var.executor_client_timeout
  ui_cpu                      = var.ui_cpu
  ui_memory                   = var.ui_memory
  temporal_cpu                = var.temporal_cpu
  temporal_memory             = var.temporal_memory
  temporal_client_rpc_timeout = var.temporal_client_rpc_timeout
  temporal_num_history_shards = var.temporal_num_history_shards
  caddy_cpu                   = var.caddy_cpu
  caddy_memory                = var.caddy_memory
  db_instance_class           = var.db_instance_class
  db_instance_size            = var.db_instance_size
}
