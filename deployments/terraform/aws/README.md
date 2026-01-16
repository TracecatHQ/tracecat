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
# (Optional) AWS role to assume
export AWS_ROLE_ARN="arn:aws:iam::123456789012:role/YourRole"

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

# 3. (Optional) Add Temporal Cloud API key to AWS Secrets Manager
aws secretsmanager create-secret --name tracecat/temporal-api-key \
  --secret-string "your-temporal-cloud-api-key"

# 4. Store secret ARNs in variables
tracecat_secrets_arn=$(aws secretsmanager describe-secret --secret-id tracecat/secrets | jq -r '.ARN')
temporal_cloud_api_key_secret_arn=""
if aws secretsmanager describe-secret --secret-id tracecat/temporal-api-key >/dev/null 2>&1; then
  temporal_cloud_api_key_secret_arn=$(aws secretsmanager describe-secret --secret-id tracecat/temporal-api-key | jq -r '.ARN')
fi

echo "Tracecat secrets ARN: $tracecat_secrets_arn"
if [ -n "$temporal_cloud_api_key_secret_arn" ]; then
  echo "Temporal Cloud API key ARN: $temporal_cloud_api_key_secret_arn"
else
  echo "Temporal Cloud API key ARN: (not set)"
fi

# 5. Run Terraform to deploy Tracecat
export TF_VAR_tracecat_secrets_arn=$tracecat_secrets_arn
export TF_VAR_temporal_cloud_api_key_secret_arn=$temporal_cloud_api_key_secret_arn
export TF_VAR_domain_name=$DOMAIN_NAME
export TF_VAR_hosted_zone_id=$HOSTED_ZONE_ID
export TF_VAR_aws_region=$AWS_REGION
export TF_VAR_aws_account_id=$AWS_ACCOUNT_ID
# Optional
export TF_VAR_aws_role_arn=$AWS_ROLE_ARN

terraform init
terraform apply
```
