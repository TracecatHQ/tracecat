# Tracecat EKS Terraform stack

Deploy Tracecat on AWS EKS with managed services (RDS PostgreSQL, ElastiCache Redis, S3).

## Prerequisites

- Terraform
- `openssl` to create cryptographic keys used in the Tracecat app
- Route53 hosted zone for your domain
- AWS credentials
- AWS CLI
- `jq` to parse JSON from AWS CLI output

## Resources

The Terraform stack is split into two modules: `network` and `eks`.

`network` deploys:
- VPC

`eks` deploys:
- EKS cluster

Note: the `eks` module is agnostic to `network` module.
It can be deployed into any VPC.

This stack always provisions RDS, ElastiCache, and S3. The only deployment mode toggle is Temporal (`temporal_mode` = `self-hosted` or `cloud`). It also installs External Secrets Operator to sync AWS Secrets Manager secrets into Kubernetes, including the generated `rediss://` Redis URL.

Network hardening:
- RDS and ElastiCache are exclusively restricted to pods with SecurityGroupPolicy labels (VPC CNI Pod ENI mode). There is no VPC CIDR fallback; node instance types must support trunk ENI.
- CoreDNS access rules are added to the cluster security group so SGP-labeled pods can resolve DNS.
- S3 access is limited to the Tracecat IRSA role via bucket policies; pods must use the chart's service account.

## Default deployment profile (~100 concurrent users)



## Capacity strategy (on-demand vs spot)

The EKS module uses managed node groups with capacity labels to steer scheduling:

- **On-demand node group (always on):** `capacity_type = ON_DEMAND`, labeled `tracecat.com/capacity=on-demand`.
- **Spot node group (default):** enabled with `spot_node_group_enabled=true`, `capacity_type = SPOT`, labeled `tracecat.com/capacity=spot`.

When the spot node group is enabled, Terraform injects scheduling defaults into the Tracecat Helm chart:

- **Preferred node affinity** for `tracecat.com/capacity=spot` (soft preference).
- **Topology spread** across `tracecat.com/capacity` with `whenUnsatisfiable=ScheduleAnyway` to balance across on-demand and spot when both are available.

You can disable spot by setting `spot_node_group_enabled=false` or change the mix by adjusting the on-demand and spot sizes.

Terraform includes plan-time capacity guardrails that verify the desired node count can support the configured replicas and resource requests at rollout peak (with a 25% surge). If capacity is insufficient, `terraform plan` will emit a warning. See `modules/eks/main.tf` for the check blocks.

## Light deployment profile

- Nodes: `3 x m7g.xlarge`
- Requested capacity: `9.75 vCPU`, `23 GiB RAM`
- Scheduled capacity: `12 vCPU`, `48 GiB RAM`
- Percent headroom: `~15%` (`1.8 vCPU`, `28 GiB RAM`)
- Cost estimate: `$1000/month`

Set these Terraform variables:

```bash
# Replica counts
api_replicas=2
worker_replicas=2
executor_replicas=2
agent_executor_replicas=1
ui_replicas=1

# Resource requests (requests == limits)
api_cpu_request_millicores=500
api_memory_request_mib=1024
worker_cpu_request_millicores=500
worker_memory_request_mib=1024
executor_cpu_request_millicores=750
executor_memory_request_mib=2048
agent_executor_cpu_request_millicores=500
agent_executor_memory_request_mib=1024
ui_cpu_request_millicores=250
ui_memory_request_mib=512

# Node groups: 3 on-demand, all m7g.xlarge
node_instance_types='["m7g.xlarge"]'
node_ami_type="AL2023_ARM_64_STANDARD"
node_min_size=3
node_desired_size=3
node_max_size=4
spot_node_group_enabled=false

# Capacity guardrail inputs
node_schedulable_cpu_millicores_per_node=4000
node_schedulable_memory_mib_per_node=16384
capacity_reserved_cpu_millicores=3000
capacity_reserved_memory_mib=8192

# Persistence services
rds_instance_class="db.t4g.medium"
elasticache_node_type="cache.t4g.small"
rds_database_insights_mode="standard"
```

## How to deploy

```bash
# 1. Set domain name, hosted zone ID, AWS region,
# and AWS account ID to deploy into
export DOMAIN_NAME="tracecat.example.com"
export AWS_REGION="us-west-2"
export AWS_ACCOUNT_ID="123456789012"
# (Optional) AWS role name to assume for cross-account deploys.
# If you set AWS_ROLE_NAME, you must also set AWS_ACCOUNT_ID.
export AWS_ROLE_NAME="YourRole"

# Either hardcode or use AWS CLI to get hosted zone ID
hosted_zone_id=$(aws route53 list-hosted-zones | jq -r '.HostedZones[] | select(.Name == "'$DOMAIN_NAME'.") | .Id')
export HOSTED_ZONE_ID=$hosted_zone_id

# 2. Create Tracecat secret in AWS Secrets Manager
aws secretsmanager create-secret --name tracecat/secrets \
  --secret-string '{
   "dbEncryptionKey": "'$(openssl rand -base64 32 | tr '+/' '-_')'",
   "serviceKey": "'$(openssl rand -hex 32)'",
   "signingSecret": "'$(openssl rand -hex 32)'",
   "userAuthSecret": "'$(openssl rand -hex 32)'"
}'

# 3. Store secret ARNs in variables
tracecat_secrets_arn=$(aws secretsmanager describe-secret --secret-id tracecat/secrets | jq -r '.ARN')

# 4. Run Terraform to deploy Tracecat
export TF_VAR_tracecat_secrets_arn=$tracecat_secrets_arn
export TF_VAR_domain_name=$DOMAIN_NAME
export TF_VAR_hosted_zone_id=$HOSTED_ZONE_ID
export TF_VAR_aws_region=$AWS_REGION
export TF_VAR_aws_account_id=$AWS_ACCOUNT_ID
# Optional
export TF_VAR_aws_role_name=$AWS_ROLE_NAME

terraform init
terraform apply
```

## Temporal self-hosted vs external cluster

To deploy Temporal in self-hosted mode, set `temporal_mode` to `self-hosted`.

```bash
temporal_mode="self-hosted"
```

To connect to an external Temporal cluster (cloud or self-hosted), set `temporal_mode` to `cloud`.
Then configure the Temporal cluster URL, namespace, and API key (if required).

Create an API key (if required) and store it in AWS Secrets Manager.
```bash
aws secretsmanager create-secret --name tracecat/temporal-api-key --secret-string "your-temporal-api-key"
```

The deployment variables are:

```bash
cluster_url="us-west-2.aws.api.temporal.io:7233"
temporal_cluster_namespace="my-temporal-namespace"
temporal_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:my-temporal-api-key-secret"
```

## Snapshots and restore

To restore the RDS instance from an existing snapshot, set `rds_snapshot_identifier` to the snapshot identifier or ARN before running `terraform apply`.

```bash
rds_snapshot_identifier="my-rds-snapshot-id"
```

Notes:
- The master username is inherited from the snapshot and cannot be changed. Set `rds_master_username` to match the snapshot's master username so post-restore jobs can connect.
- `rds_allocated_storage` must be at least the snapshot size. You can't restore with less storage, and increases must be at least 10%.
- For shared manual snapshots, use the full snapshot ARN as the identifier.
