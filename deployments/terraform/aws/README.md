# Tracecat EKS Terraform stack

Deploy Tracecat on AWS EKS with managed services (RDS PostgreSQL, ElastiCache Redis, S3).

## Prerequisites

- Terraform 1.11.0
- AWS CLI v2
- `openssl` to create cryptographic keys used in the Tracecat app
- Route53 hosted zone for your domain
- AWS credentials with permissions to create EKS, RDS, ElastiCache, S3, IAM, VPC, ACM, WAF, and Route53 resources
- `jq` to parse JSON from AWS CLI output

## Resources

The Terraform stack is split into two modules: `network` and `eks`.

- `network` module: VPC, subnets, NAT gateways, ACM certificate
- `eks` module: EKS cluster, node groups, add-ons, RDS, ElastiCache, S3, Tracecat Helm release

This stack always provisions RDS, ElastiCache, and S3. The only deployment mode toggle is Temporal (`temporal_mode` = `self-hosted` or `cloud`). It also installs External Secrets Operator to sync AWS Secrets Manager secrets into Kubernetes, including the generated `rediss://` Redis URL.

Network hardening:
- RDS and ElastiCache are exclusively restricted to pods with SecurityGroupPolicy labels (VPC CNI Pod ENI mode). There is no VPC CIDR fallback; node instance types must support trunk ENI.
- CoreDNS access rules are added to the cluster security group so SGP-labeled pods can resolve DNS.
- S3 access is limited to the Tracecat IRSA role via bucket policies; pods must use the chart's service account.

## Default deployment profile (~100 concurrent users)

- Nodes: `10 x m7g.2xlarge` (`8` on-demand + `2` spot by default)
- Guardrail requirement (rollout peak + reserved headroom): `46.5 vCPU`, `95.5 GiB RAM`
- Scheduled capacity (on-demand only): `64 vCPU`, `256 GiB RAM`
- Scheduled capacity (on-demand + spot): `80 vCPU`, `320 GiB RAM`
- Percent headroom (on-demand only): `~27% CPU`, `~63% RAM` (`17.5 vCPU`, `160.5 GiB RAM`)
- Estimated cost: `$4500/month`

Definitions used above:
- `guardrail model`: The plan-time capacity checks in `deployments/terraform/aws/modules/eks/main.tf` that compare required capacity vs configured node capacity.
- `rollout peak`: Replica requests with rollout surge applied (`rollout_surge_percent`, default `25`), across API/worker/executor/agentExecutor/UI.
- `reserved headroom`: Extra fixed capacity reserved for system/auxiliary workloads (`capacity_reserved_cpu_millicores`, `capacity_reserved_memory_mib`, `capacity_reserved_pod_eni`).

```bash
# Replica counts
api_replicas=2
worker_replicas=4
executor_replicas=4
agent_executor_replicas=2
ui_replicas=2

# Resource requests (requests == limits)
api_cpu_request_millicores=2000
api_memory_request_mib=4096
worker_cpu_request_millicores=2000
worker_memory_request_mib=2048
executor_cpu_request_millicores=4000
executor_memory_request_mib=8192
agent_executor_cpu_request_millicores=2000
agent_executor_memory_request_mib=8192
ui_cpu_request_millicores=500
ui_memory_request_mib=512

# Node groups: 8 on-demand + 2 spot, all m7g.2xlarge
node_instance_types='["m7g.2xlarge"]'
node_ami_type="AL2023_ARM_64_STANDARD"
node_min_size=8
node_desired_size=8
node_max_size=12
spot_node_group_enabled=true
spot_node_instance_types='["m7g.2xlarge"]'
spot_node_min_size=2
spot_node_desired_size=2
spot_node_max_size=4

# Capacity guardrail inputs
node_schedulable_cpu_millicores_per_node=8000
node_schedulable_memory_mib_per_node=32768
pod_eni_capacity_per_node=16
rollout_surge_percent=25
capacity_reserved_cpu_millicores=3000
capacity_reserved_memory_mib=8192
capacity_reserved_pod_eni=8

# Persistence services and Temporal mode
rds_instance_class="db.t4g.xlarge"
rds_allocated_storage=50
rds_storage_type="gp3"
elasticache_node_type="cache.t4g.medium"
rds_database_insights_mode="advanced"
```

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
- Guardrail requirement (rollout peak + reserved headroom): `9.75 vCPU`, `23 GiB RAM`
- Scheduled capacity (on-demand only): `12 vCPU`, `48 GiB RAM`
- Percent headroom (on-demand only): `~19% CPU`, `~52% RAM` (`2.25 vCPU`, `25 GiB RAM`)
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
rds_instance_class="db.m7g.large"
rds_allocated_storage=100
rds_storage_type="gp3"
elasticache_node_type="cache.m7g.large"
rds_database_insights_mode="standard"
```

## How to deploy

### 1. Configure variables

```bash
export DOMAIN_NAME="tracecat.example.com"
export AWS_REGION="us-west-2"
export SUPERADMIN_EMAIL="admin@example.com"

# (Optional) For cross-account deploys, both must be set together.
export AWS_ACCOUNT_ID="123456789012"
export AWS_ROLE_NAME="YourRole"

# Look up hosted zone ID (strip the /hostedzone/ prefix)
export HOSTED_ZONE_ID=$(aws route53 list-hosted-zones \
  | jq -r '.HostedZones[] | select(.Name == "'$DOMAIN_NAME'.") | .Id | split("/") | last')
```

### 2. Create Tracecat secret in AWS Secrets Manager

```bash
aws secretsmanager create-secret --name tracecat/secrets \
  --secret-string '{
   "dbEncryptionKey": "'$(openssl rand -base64 32 | tr '+/' '-_')'",
   "serviceKey": "'$(openssl rand -hex 32)'",
   "signingSecret": "'$(openssl rand -hex 32)'",
   "userAuthSecret": "'$(openssl rand -hex 32)'"
}'

tracecat_secrets_arn=$(aws secretsmanager describe-secret \
  --secret-id tracecat/secrets | jq -r '.ARN')
```

### 3. Export Terraform variables

```bash
export TF_VAR_domain_name=$DOMAIN_NAME
export TF_VAR_hosted_zone_id=$HOSTED_ZONE_ID
export TF_VAR_aws_region=$AWS_REGION
export TF_VAR_superadmin_email=$SUPERADMIN_EMAIL
export TF_VAR_tracecat_secrets_arn=$tracecat_secrets_arn

# Optional
export TF_VAR_aws_account_id=$AWS_ACCOUNT_ID
export TF_VAR_aws_role_name=$AWS_ROLE_NAME
```

### 4. Deploy (3-stage first-time apply)

First-time deployments require 3 targeted applies due to provider and CRD bootstrapping dependencies. Subsequent applies (upgrades, config changes) only need a single `terraform apply`.

**Why 3 stages?**

- The Kubernetes/Helm providers reference the EKS cluster endpoint, which doesn't exist yet.
- `kubernetes_manifest` resources validate CRDs at plan time, but the External Secrets Operator and VPC CNI (which install those CRDs) haven't been deployed yet.
- The Tracecat Helm release depends on the `kubernetes_manifest` resources.

```bash
terraform init

# Stage 1 — Bootstrap EKS cluster and security groups.
# Creates the cluster so the Kubernetes/Helm providers can connect,
# and materializes security group IDs so for_each is plannable.
# Network module resources (VPC, subnets, NAT, ACM) are created
# automatically as dependencies.
terraform apply \
  -target=module.eks.aws_eks_cluster.tracecat \
  -target=module.eks.aws_security_group.tracecat_postgres_client \
  -target=module.eks.aws_security_group.tracecat_redis_client

# Stage 2 — Deploy infrastructure and install CRDs.
# Creates node groups, RDS, ElastiCache, S3, and Helm charts
# (External Secrets Operator, ExternalDNS, ALB Controller, Reloader).
# Skips kubernetes_manifest resources (CRD validation fails at plan
# time) and helm_release.tracecat (depends on them).
terraform apply \
  -target=module.eks.aws_eks_node_group.tracecat \
  -target=module.eks.aws_eks_addon.vpc_cni \
  -target=module.eks.aws_eks_addon.coredns \
  -target=module.eks.aws_eks_addon.kube_proxy \
  -target=module.eks.helm_release.external_secrets \
  -target=module.eks.helm_release.external_dns \
  -target=module.eks.helm_release.aws_load_balancer_controller \
  -target=module.eks.helm_release.reloader \
  -target=module.eks.aws_db_instance.tracecat \
  -target=module.eks.aws_elasticache_replication_group.tracecat \
  -target=module.eks.aws_secretsmanager_secret_version.redis_url \
  -target=module.eks.kubernetes_job_v1.create_temporal_databases

# Stage 3 — Full apply.
# CRDs are now installed. Creates the remaining kubernetes_manifest
# resources (ClusterSecretStore, ExternalSecret, SecurityGroupPolicy)
# and deploys the Tracecat Helm release.
terraform apply
```

## Outbound allowlisting (Elastic, etc.)

If a third-party service needs Tracecat egress IPs (for example, Elastic IP allowlists), use the NAT Gateway EIPs:

```bash
terraform output nat_gateway_eips
```

These are the stable outbound IPs for workloads running in private subnets (including `executor` and `agent-executor`).

If `spot_node_group_enabled=true` (default), add this target to stage 2:

```bash
  -target='module.eks.aws_eks_node_group.tracecat_spot[0]'
```

If `enable_waf=true` (default), add this target to stage 2:

```bash
  -target='module.eks.aws_wafv2_web_acl.main[0]'
```

## Multi-region and multi-account deployments

IAM role names derived from `cluster_name` are **globally unique per AWS account** (e.g., `tracecat-eks-cluster-role`). When deploying multiple instances in the same account, set a unique `cluster_name` per deployment to avoid collisions:

```bash
# us-east-2 (default)
export TF_VAR_cluster_name="tracecat-eks"

# eu-west-1
export TF_VAR_cluster_name="tracecat-eks-eu"
```

The cluster name is used as a prefix for EKS cluster, IAM roles, security groups, S3 buckets, WAF rules, and ALB group names.

## EKS cluster authentication

The EKS cluster is created with `authentication_mode = API`, which uses the IAM access entry API exclusively. This avoids the `aws-auth` ConfigMap attack surface (any principal with `kube-system` ConfigMap write access can escalate to cluster admin). Access entries are IAM-controlled and CloudTrail-audited:

```bash
# Example: grant admin access to an SSO role
aws eks create-access-entry \
  --cluster-name tracecat-eks \
  --principal-arn arn:aws:iam::123456789012:role/YourSSORole

aws eks associate-access-policy \
  --cluster-name tracecat-eks \
  --principal-arn arn:aws:iam::123456789012:role/YourSSORole \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
  --access-scope type=cluster
```

## Temporal self-hosted vs external cluster

To deploy Temporal in self-hosted mode, set `temporal_mode` to `self-hosted`.

```bash
export TF_VAR_temporal_mode="self-hosted"
```

Default persistence profile:
`rds_instance_class="db.t4g.xlarge"`, `rds_allocated_storage=50`, `rds_storage_type="gp3"`, `elasticache_node_type="cache.t4g.medium"` (3-node Multi-AZ replication group with automatic failover), and `multi_az=true` for RDS.

For Temporal self-hosting, use a larger starting profile:

```bash
export TF_VAR_rds_instance_class="db.r7g.4xlarge"
export TF_VAR_rds_allocated_storage=500
export TF_VAR_rds_storage_type="io2"
export TF_VAR_elasticache_node_type="cache.r7g.xlarge"
```

To connect to an external Temporal cluster (cloud or self-hosted), set `temporal_mode` to `cloud`.
Then configure the Temporal cluster URL, namespace, and API key (if required).

Create an API key (if required) and store it in AWS Secrets Manager.
```bash
aws secretsmanager create-secret --name tracecat/temporal-api-key --secret-string "your-temporal-api-key"
```

The deployment variables are:

```bash
export TF_VAR_temporal_mode="cloud"
export TF_VAR_temporal_cluster_url="us-west-2.aws.api.temporal.io:7233"
export TF_VAR_temporal_cluster_namespace="my-temporal-namespace"
export TF_VAR_temporal_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:tracecat/temporal-api-key-AbCdEf"
```

## Observability (Grafana Cloud)

The stack includes an optional observability pipeline that deploys:

1. **Grafana K8s Monitoring Helm chart** (Alloy-based) for in-cluster metrics and logs
2. **CloudWatch Metric Streams + Kinesis Firehose** for AWS service metrics (RDS, ElastiCache, ALB, WAF, S3)
3. **ESO ExternalSecret** for syncing Grafana Cloud credentials into the cluster
4. **IAM roles** with confused-deputy protections for CloudWatch and Firehose

All observability resources are gated by `enable_observability` (default `false`).

### 1. Create a Grafana Cloud credentials secret

Store your Grafana Cloud metrics write token in AWS Secrets Manager:

```bash
aws secretsmanager create-secret --name tracecat/grafana-cloud \
  --secret-string '{"metrics_write_token": "glc_..."}'

grafana_cloud_credentials_arn=$(aws secretsmanager describe-secret \
  --secret-id tracecat/grafana-cloud | jq -r '.ARN')
```

### 2. Set Terraform variables

```bash
export TF_VAR_enable_observability=true
export TF_VAR_grafana_cloud_prometheus_url="https://prometheus-prod-01-us-east-0.grafana.net/api/prom/push"
export TF_VAR_grafana_cloud_prometheus_username="123456"
export TF_VAR_grafana_cloud_loki_url="https://logs-prod-us-east-0.grafana.net/loki/api/v1/push"
export TF_VAR_grafana_cloud_loki_username="789012"
export TF_VAR_grafana_cloud_credentials_secret_arn="$grafana_cloud_credentials_arn"
export TF_VAR_grafana_cloud_firehose_endpoint="https://awsmetrics-prod-01-us-east-0.grafana.net/api/v1/push"
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `enable_observability` | Yes | `false` | Master toggle for all observability resources |
| `grafana_cloud_prometheus_url` | Yes | `""` | Prometheus remote write URL |
| `grafana_cloud_prometheus_username` | Yes | `""` | Prometheus numeric instance ID |
| `grafana_cloud_loki_url` | Yes | `""` | Loki push URL |
| `grafana_cloud_loki_username` | Yes | `""` | Loki numeric instance ID |
| `grafana_cloud_credentials_secret_arn` | Yes | `""` | Secrets Manager ARN containing `{"metrics_write_token": "..."}` |
| `grafana_cloud_firehose_endpoint` | Yes | `""` | Firehose endpoint URL for CloudWatch Metric Streams |
| `observability_log_retention_days` | No | `30` | Retention for Firehose logs and S3 failed-delivery backups |

### First-time deploy note

If enabling observability on a fresh deployment, add these targets to stage 2:

```bash
  -target='module.eks.kubernetes_namespace.observability[0]' \
  -target='module.eks.helm_release.grafana_k8s_monitoring[0]'
```

## Snapshots and restore

To restore the RDS instance from an existing snapshot, set `rds_snapshot_identifier` to the snapshot identifier or ARN before running `terraform apply`.

```bash
export TF_VAR_rds_snapshot_identifier="my-rds-snapshot-id"
```

Notes:
- The master username is inherited from the snapshot and cannot be changed. Set `rds_master_username` to match the snapshot's master username so post-restore jobs can connect.
- `rds_allocated_storage` must be at least the snapshot size. You can't restore with less storage, and increases must be at least 10%.
- For shared manual snapshots, use the full snapshot ARN as the identifier.
