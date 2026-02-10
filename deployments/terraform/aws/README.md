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

## Capacity strategy (on-demand vs spot)

The EKS module uses managed node groups with capacity labels to steer scheduling:

- **On-demand node group (always on):** `capacity_type = ON_DEMAND`, labeled `tracecat.com/capacity=on-demand`.
- **Spot node group (default):** enabled with `spot_node_group_enabled=true`, `capacity_type = SPOT`, labeled `tracecat.com/capacity=spot`.

When the spot node group is enabled, Terraform injects scheduling defaults into the Tracecat Helm chart:

- **Preferred node affinity** for `tracecat.com/capacity=spot` (soft preference).
- **Topology spread** across `tracecat.com/capacity` with `whenUnsatisfiable=ScheduleAnyway` to balance across on-demand and spot when both are available.

This is a preference, not a hard guarantee. If spot capacity is unavailable, pods will still schedule onto on-demand nodes (as long as there is capacity).

The on-demand/spot mix is controlled by the node group sizes. There is no cluster autoscaler installed by default, so the ratio is static unless you change the node group sizes via Terraform.

Default node split (8 nodes, 75/25 on-demand/spot):

```bash
export TF_VAR_node_min_size=6
export TF_VAR_node_desired_size=6
export TF_VAR_spot_node_min_size=2
export TF_VAR_spot_node_desired_size=2
```

You can disable spot by setting `spot_node_group_enabled=false` or change the mix by adjusting the on-demand and spot sizes.

Terraform includes plan-time capacity guardrails that verify the desired node count can support the configured replicas and resource requests at rollout peak (with a 25% surge). If capacity is insufficient, `terraform plan` will emit a warning. See `modules/eks/main.tf` for the check blocks.

## Resource sizing notes (with/without Temporal)

These estimates are based on Kubernetes **resource requests** from the chart defaults and Terraform overrides. Actual usage varies; leave headroom for system add-ons (CoreDNS, VPC CNI, External Secrets, AWS Load Balancer Controller, Reloader) and for Temporal when self-hosting.

**Chart defaults (`helm/tracecat/values.yaml`)** are tuned for local development:
- **Replicas:** ui=1, api=1, worker=1, executor=1, agentExecutor=1.
- **Requests per replica:** ui=100m/256Mi; api/worker/executor/agentExecutor=250m/512Mi.
- **Tracecat total requests (chart defaults):** ~1.1 vCPU / 2.25Gi (Temporal not included).
These defaults apply to manual Helm installs; Terraform overrides them for AWS production.

**Terraform (AWS) production overrides** are defined via Terraform variables (see `variables.tf`). Resource requests equal limits (Guaranteed QoS):
- **Replicas:** api=2, worker=4, executor=4, agentExecutor=2, ui=2.
- **Requests per replica:** api=2 vCPU/4Gi; worker=2 vCPU/2Gi; executor=4 vCPU/8Gi; agentExecutor=4 vCPU/8Gi; ui=0.5 vCPU/0.5Gi.
- **Tracecat steady-state requests:** ~37 vCPU / 65Gi (Temporal not included).
- **Rollout peak (25% surge):** ~49.5 vCPU / 87.5Gi + configurable reserve for system workloads.

**Temporal capacity:** the Temporal subchart does not set explicit resource requests by default. If `temporal_mode=self-hosted`, budget extra capacity (for the Temporal server + schema/admintools job), or set `temporal.server.resources` explicitly.

**Recommended node capacity:**
- **With self-hosted Temporal:** plan for **8× `m7g.2xlarge`** total nodes (e.g., 6 on-demand + 2 spot). This fits the production profile at rollout peak with headroom.
- **Without Temporal (external):** you can run with **6× `m7g.2xlarge`** on-demand, but 8 nodes (6+2 spot) provides headroom for rolling updates and system workloads.


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
export TF_VAR_temporal_mode="self-hosted"
```

To connect to an external Temporal cluster (cloud or self-hosted), set `temporal_mode` to `cloud`.
Then configure the Temporal cluster URL, namespace, and API key (if required).

Create an API key (if required) and store it in AWS Secrets Manager.
```bash
aws secretsmanager create-secret --name tracecat/temporal-api-key --secret-string "your-temporal-api-key"
```

The deployment variables are:

```bash
export TF_VAR_temporal_cluster_url="us-west-2.aws.api.temporal.io:7233"
export TF_VAR_temporal_cluster_namespace="my-temporal-namespace"
export TF_VAR_temporal_secret_arn="arn:aws:secretsmanager:us-east-1:123456789012:secret:my-temporal-api-key-secret"
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
