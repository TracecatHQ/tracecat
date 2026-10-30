# Tracecat AWS ECS Fargate stack

Terraform stack for Tracecat on AWS ECS Fargate (`>1.0.0-beta.xx`).

## Services

- `api`
- `worker`
- `background-worker` (durable platform tasks, including Git sync)
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
- S3 buckets: attachments, registry, workflow artifacts, agent filesystem snapshots
- VPC endpoints: S3 + Secrets Manager

## Security

> [!NOTE]
> Fargate does not support the permissions model required by `nsjail`, so `core.script.run_python` and executor code paths run without `nsjail` isolation.
> Tracecat uses `unsafe_pid_executor` fallback in this mode. It attempts PID namespace isolation with `unshare --pid`, but this is typically unavailable on Fargate.
> As a result, script execution uses subprocess isolation without nsjail-level mount/network/cgroup controls.
> If you need highest isolation for untrusted code execution, deploy Tracecat on Kubernetes with the Helm chart, where `nsjail` is enabled by default.

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
export TF_VAR_tracecat_image_tag=1.3.0

terraform apply
```

For Terraform Cloud direct OIDC runs, the target account and role come from `TFC_AWS_RUN_ROLE_ARN`. This stack now uses the ambient AWS credentials from the execution environment and no longer accepts `aws_account_id` / `aws_role_name` inputs for a second provider-side assume-role hop.

## Self-contained migrations

Application migrations enable and validate pgvector and `pg_trgm` in the
application RDS database. The default init container uses the existing managed
master credentials for `core_database`; no new role or privilege grant is needed.
The PostgreSQL 16.10 default supports both extensions; no engine-version change
is required. If an RDS allowlist excludes `vector` or `pg_trgm`, an authorized
administrator must first allow them in `rds.allowed_extensions`. For restricted
migration roles, an administrator must preinstall the extensions before rerunning
migrations. See the
[application database prerequisite and upgrade guide](../postgres/README.md).
Use the `core_database` connection, not the Temporal RDS connection; Temporal
does not need either extension.

- API task startup includes an internal migrations init container.
- API container starts only if migrations succeed (`dependsOn: SUCCESS`).
- Terraform waits for healthy API tasks (`wait_for_steady_state = true`) before updating dependent services. A migration failure prevents the API rollout from becoming healthy; the wait fails instead of starting the new worker revision.
- `worker`, `background-worker`, `executor`, and `agent-executor` are ordered after API in Terraform, so service updates do not proceed past API if migrations fail.

By default, the migrations init container uses the same backend image repository
and tag as the application. Set these optional variables to decouple it:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `tracecat_migrations_image` | `tracecat_image` | Optional migrations image repository override |
| `tracecat_migrations_image_tag` | `tracecat_image_tag` | Optional migrations image tag override |

For app-only rollbacks, keep the migrations image on a version that understands
the current Alembic database revision while rolling back the app images.

## Useful outputs

- `public_app_url`
- `public_api_url`
- `ecs_cluster_name`
- `s3_attachments_bucket_name`
- `s3_registry_bucket_name`
- `s3_workflow_bucket_name`
- `s3_agent_bucket_name`
- `nat_gateway_eips` (outbound allowlisting IPs; public identifiers, not secrets)


## Durable background tasks

The `background-worker` service runs `tracecat.background.worker` on a dedicated,
generic Temporal queue. `background_queue` defaults to `shared-background-queue`
and is passed to both the API and worker. Temporal creates the queue on demand;
no server provisioning is required. Default sizing is one task, 1024 CPU units,
2048 MiB, and four concurrent activities. Use `background_worker_desired_count`,
`background_worker_cpu`, `background_worker_memory`, and
`background_worker_max_concurrent_activities` to tune it independently.

Deploy an app image containing the worker together with the additive
`workspace_sync_operation` migration. The worker shares the existing API task
role, database, and workflow artifact bucket, and uses the worker-specific
`worker_secrets` set. API-only authentication, OAuth, SAML, and SMTP secrets are
not passed to the worker. Preserve artifacts for at
least the 24-hour preview confirmation window. API acceptance is durable even
while the worker is unavailable; operations remain queued until it returns.
Roll back application images without removing the operation table or artifacts.


### Upgrading the Git sync lifecycle

Keep `background_worker_desired_count` at one or more. Apply the additive database
migration before starting the updated application services. Terraform waits for
the API deployment to become healthy before updating the background worker. The
API cannot become healthy until its migration container succeeds.

This configuration requires Terraform 1.2 or newer for its startup precondition.
When `background_worker_desired_count` is positive, `api_desired_count` must also
be positive so at least one API task can run migrations. Set both counts to zero
to pause the API and background sync. A standalone worker with a separately managed migration
step is not supported by this configuration.

New sync requests can remain queued briefly while the worker updates. Verify
that the updated worker is polling the configured queue before declaring the
rollout healthy.

The updated worker registers both the old phase workflow and the new lifecycle
workflow. This lets an already-running old import finish. A preview prepared by
the old application must be created again before you confirm it in the new UI.
The new API accepts an operation only after Temporal records it. Temporal then
keeps its preparation, confirmation, retry, and expiration in one workflow.
The database keeps history and write receipts, not a queue that starts workflows.

Keep the operation table and stored preview artifacts when rolling back. Let
running lifecycle workflows finish on a worker that understands them before
removing that worker version. After an import commits, the worker retries pending
schedule and background updates automatically; it does not import those resources again.
