# Network Module - VPC, Subnets, NAT Gateways, ACM Certificate
module "network" {
  source = "./modules/network"

  aws_region     = var.aws_region
  domain_name    = var.domain_name
  hosted_zone_id = var.hosted_zone_id
  vpc_cidr       = var.vpc_cidr
  az_count       = var.az_count
  cluster_name   = var.cluster_name
  tags           = var.tags
}

# EKS Module - Cluster, Node Groups, Add-ons, Data Services, Tracecat Helm Release
module "eks" {
  source = "./modules/eks"

  # Cluster Configuration
  cluster_name    = var.cluster_name
  cluster_version = var.cluster_version

  # Network Configuration
  vpc_id              = module.network.vpc_id
  private_subnet_ids  = module.network.private_subnet_ids
  public_subnet_ids   = module.network.public_subnet_ids
  acm_certificate_arn = module.network.acm_certificate_arn

  # Node Group Configuration
  node_instance_types      = var.node_instance_types
  node_ami_type            = var.node_ami_type
  node_desired_size        = var.node_desired_size
  node_min_size            = var.node_min_size
  node_max_size            = var.node_max_size
  node_disk_size           = var.node_disk_size
  spot_node_group_enabled  = var.spot_node_group_enabled
  spot_node_instance_types = var.spot_node_instance_types
  spot_node_desired_size   = var.spot_node_desired_size
  spot_node_min_size       = var.spot_node_min_size
  spot_node_max_size       = var.spot_node_max_size

  # Tracecat Configuration
  domain_name            = var.domain_name
  hosted_zone_id         = var.hosted_zone_id
  tracecat_version       = var.tracecat_version
  tracecat_image_tag     = var.tracecat_image_tag
  tracecat_ingress_split = var.tracecat_ingress_split
  superadmin_email       = var.superadmin_email

  # Tracecat Secrets (AWS Secrets Manager ARN)
  tracecat_secrets_arn = var.tracecat_secrets_arn

  # Data Services Configuration
  rds_instance_class         = var.rds_instance_class
  rds_allocated_storage      = var.rds_allocated_storage
  rds_master_username        = var.rds_master_username
  rds_snapshot_identifier    = var.rds_snapshot_identifier
  rds_database_insights_mode = var.rds_database_insights_mode
  rds_skip_final_snapshot    = var.rds_skip_final_snapshot
  rds_deletion_protection    = var.rds_deletion_protection
  elasticache_node_type      = var.elasticache_node_type

  # Temporal Configuration
  temporal_mode                         = var.temporal_mode
  temporal_cluster_url                  = var.temporal_cluster_url
  temporal_cluster_namespace            = var.temporal_cluster_namespace
  temporal_secret_arn                   = var.temporal_secret_arn
  external_secrets_namespace            = var.external_secrets_namespace
  external_secrets_service_account_name = var.external_secrets_service_account_name
  external_dns_namespace                = var.external_dns_namespace
  external_dns_service_account_name     = var.external_dns_service_account_name

  # Replica Counts
  api_replicas                             = var.api_replicas
  worker_replicas                          = var.worker_replicas
  executor_replicas                        = var.executor_replicas
  executor_queue                           = var.executor_queue
  executor_backend                         = var.executor_backend
  agent_executor_replicas                  = var.agent_executor_replicas
  agent_executor_queue                     = var.agent_executor_queue
  agent_executor_backend                   = var.agent_executor_backend
  ui_replicas                              = var.ui_replicas
  api_cpu_request_millicores               = var.api_cpu_request_millicores
  api_memory_request_mib                   = var.api_memory_request_mib
  worker_cpu_request_millicores            = var.worker_cpu_request_millicores
  worker_memory_request_mib                = var.worker_memory_request_mib
  executor_cpu_request_millicores          = var.executor_cpu_request_millicores
  executor_memory_request_mib              = var.executor_memory_request_mib
  agent_executor_cpu_request_millicores    = var.agent_executor_cpu_request_millicores
  agent_executor_memory_request_mib        = var.agent_executor_memory_request_mib
  ui_cpu_request_millicores                = var.ui_cpu_request_millicores
  ui_memory_request_mib                    = var.ui_memory_request_mib
  node_schedulable_cpu_millicores_per_node = var.node_schedulable_cpu_millicores_per_node
  node_schedulable_memory_mib_per_node     = var.node_schedulable_memory_mib_per_node
  pod_eni_capacity_per_node                = var.pod_eni_capacity_per_node
  rollout_surge_percent                    = var.rollout_surge_percent
  capacity_reserved_cpu_millicores         = var.capacity_reserved_cpu_millicores
  capacity_reserved_memory_mib             = var.capacity_reserved_memory_mib
  capacity_reserved_pod_eni                = var.capacity_reserved_pod_eni

  # WAF Configuration
  enable_waf     = var.enable_waf
  waf_rate_limit = var.waf_rate_limit

  tags = var.tags

  # Enterprise Edition
  ee_multi_tenant = var.ee_multi_tenant

  # Feature Flags
  feature_flags = var.feature_flags
}
