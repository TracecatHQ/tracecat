# Tracecat AWS ECS Fargate stack

Terraform stack for Tracecat on AWS ECS Fargate (`>1.0.0-beta.xx`).

## Services

- `api`
- `worker`
- `executor`
- `agent-executor`
- `ui`
- `caddy`
- `temporal` (optional auto-setup)
- `temporal-ui` (optional)

## Infrastructure

- Public ALB + Route53 + ACM
- ECS cluster with Service Connect
- RDS (core + optional temporal DB)
- ElastiCache Redis
- S3 buckets: attachments, registry, workflow artifacts
- VPC endpoints: S3 + Secrets Manager

## Security

> [!NOTE]
> Fargate does not support the permissions model required by `nsjail`, so `core.script.run_python` and executor code paths run without `nsjail` isolation.
> Tracecat uses `unsafe_pid_executor` fallback in this mode. It attempts PID namespace isolation with `unshare --pid`, but this is typically unavailable on Fargate.
> As a result, script execution uses subprocess isolation without nsjail-level mount/network/cgroup controls.
> If you need highest isolation for untrusted code execution, deploy Tracecat on Kubernetes with the Helm chart, where `nsjail` is enabled by default.

- `TRACECAT__DISABLE_NSJAIL=true`
- `TRACECAT__EXECUTOR_BACKEND=direct` (executor + agent-executor)

## Default sizing

- `api_cpu=2048`
- `api_memory=4096`
- `worker_desired_count=2`
- `executor_desired_count=2`
- `agent_executor_desired_count=1`
- `agent_executor_cpu=4096`
- `agent_executor_memory=16384`
- `tracecat_db_instance_class=db.t4g.medium`
- `tracecat_db_allocated_storage=20`
- `temporal_db_instance_class=db.t4g.2xlarge`
- `temporal_db_allocated_storage=50`
- `temporal_cpu=8192`
- `temporal_memory=16384`
- `redis_node_type=cache.t4g.small`

For the bundled Fargate `temporalio/auto-setup` deployment, `temporal_db_force_ssl` now defaults to `false`. This disables `rds.force_ssl` only on the Temporal RDS instance, requires a DB reboot when changed, and permits non-TLS connections for the bundled Temporal service.

If you are connecting Tracecat to Temporal Cloud or another external Temporal cluster, this setting is typically irrelevant because you should also set `disable_temporal_autosetup=true` and not create the bundled Temporal RDS instance. If you do manage your own Temporal PostgreSQL outside the bundled Fargate auto-setup path and your deployment supports TLS correctly, set `temporal_db_force_ssl=true`.

## Quick start

```bash
cd deployments/fargate
terraform init

export AWS_DEFAULT_REGION=us-east-1
./scripts/create-aws-secrets.sh

export TF_VAR_aws_region=us-east-1
export TF_VAR_domain_name=tracecat.example.com
export TF_VAR_hosted_zone_id=Z1234567890
export TF_VAR_tracecat_db_encryption_key_arn=arn:aws:secretsmanager:...
export TF_VAR_tracecat_service_key_arn=arn:aws:secretsmanager:...
export TF_VAR_tracecat_signing_secret_arn=arn:aws:secretsmanager:...
export TF_VAR_tracecat_image_tag=1.0.0-beta.43

terraform apply
```

For Terraform Cloud direct OIDC runs, the target account and role come from `TFC_AWS_RUN_ROLE_ARN`. This stack now uses the ambient AWS credentials from the execution environment and no longer accepts `aws_account_id` / `aws_role_name` inputs for a second provider-side assume-role hop.

## Self-contained migrations

- API task startup includes an internal migrations init container.
- API container starts only if migrations succeed (`dependsOn: SUCCESS`).
- `worker`, `executor`, and `agent-executor` are ordered after API in Terraform, so service updates do not proceed past API if migrations fail.

## Useful outputs

- `public_app_url`
- `public_api_url`
- `ecs_cluster_name`
- `s3_attachments_bucket_name`
- `s3_registry_bucket_name`
- `s3_workflow_bucket_name`
- `nat_gateway_eips` (outbound allowlisting IPs; public identifiers, not secrets)
