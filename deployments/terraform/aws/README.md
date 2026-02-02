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
