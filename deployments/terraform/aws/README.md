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
- RDS and ElastiCache are restricted to pods labeled for access via Security Groups for Pods (VPC CNI Pod ENI mode).
- S3 access is limited to the Tracecat IRSA role via bucket policies; pods must use the chart’s service account.

## Capacity strategy (on-demand vs spot)

The EKS module uses managed node groups with capacity labels to steer scheduling:

- **On-demand node group (always on):** `capacity_type = ON_DEMAND`, labeled `tracecat.com/capacity=on-demand`.
- **Spot node group (default):** enabled with `spot_node_group_enabled=true`, `capacity_type = SPOT`, labeled `tracecat.com/capacity=spot`.

When the spot node group is enabled, Terraform injects scheduling defaults into the Tracecat Helm chart:

- **Preferred node affinity** for `tracecat.com/capacity=spot` (soft preference).
- **Topology spread** across `tracecat.com/capacity` with `whenUnsatisfiable=ScheduleAnyway` to balance across on-demand and spot when both are available.

This is a preference, not a hard guarantee. If spot capacity is unavailable, pods will still schedule onto on-demand nodes (as long as there is capacity).

The on-demand/spot mix is controlled by the node group sizes. There is no cluster autoscaler installed by default, so the ratio is static unless you change the node group sizes via Terraform.

Default 50/50 node split (4 nodes total):

```bash
export TF_VAR_node_min_size=2
export TF_VAR_node_desired_size=2
export TF_VAR_spot_node_min_size=2
export TF_VAR_spot_node_desired_size=2
```

You can disable spot by setting `spot_node_group_enabled=false` or change the mix by adjusting the on-demand and spot sizes.

## Resource sizing notes (with/without Temporal)

These estimates are based on Kubernetes **resource requests** from the chart defaults and Terraform overrides. Actual usage varies; leave headroom for system add-ons (CoreDNS, VPC CNI, External Secrets, AWS Load Balancer Controller, Reloader) and for Temporal when self-hosting.

**Terraform defaults (Helm chart via Terraform)** use the same replica counts as `helm/tracecat/values.yaml`, but Terraform overrides worker/executor resource requests in `terraform/aws/modules/eks/helm.tf`:
- **Replicas:** ui=1, api=2, worker=4, executor=2, agentExecutor=1.
- **Chart-default requests per replica:** ui/api/worker/executor/agentExecutor=1 vCPU/1Gi (chart-only total ~10 vCPU / 10Gi).
- **Terraform request overrides:** ui/api=1 vCPU/1Gi; worker=2 vCPU/4Gi; executor=4 vCPU/8Gi; agentExecutor=4 vCPU/8Gi.
- **Tracecat total requests (Terraform):** ~23 vCPU / 43Gi (Temporal not included).

**`helm/examples/values-aws.yaml`** increases replicas but uses chart-default requests:
- **Replicas:** ui=2, api=2, worker=8, executor=4, agentExecutor=2.
- **Requests per replica:** 1 vCPU / 1Gi (chart defaults).
- **Tracecat total requests:** ~18 vCPU / 18Gi (Temporal not included).

**Temporal capacity:** the Temporal subchart does not set explicit resource requests by default. If `temporal_mode=self-hosted`, budget extra capacity (for the Temporal server + schema/admintools job), or set `temporal.server.resources` explicitly.

**Recommended node capacity:**
- **With self-hosted Temporal:** plan for **4× `m7g.2xlarge`** total nodes (e.g., 2 on-demand + 2 spot). This safely fits both profiles above with headroom.
- **Without Temporal (external):** you can usually run with **3× `m7g.2xlarge`** total nodes, but 4 nodes provides safer headroom for upgrades and spikes.


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
