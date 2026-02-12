# Tracecat AWS ECS Fargate stack

This Terraform stack deploys Tracecat to AWS ECS Fargate.

The stack provisions the following services into ECS Fargate:
- `api`
- `worker`
- `executor`
- `agent-executor`
- `ui`
- `caddy`
- `temporal` (optional auto-setup)
- `temporal-ui` (optional)

## Resources deployed

- VPC networking primitives (via `modules/network`)
- ACM cert + Route53 record + internet-facing ALB
- ECS cluster with Service Connect
- ECS services/task definitions for Tracecat services listed above
- RDS Postgres for core app data
- RDS Postgres for Temporal (when auto-setup is enabled)
- ElastiCache Redis (TLS)
- S3 buckets for:
  - Attachments (`tracecat-attachments-*`)
  - Registry tarballs (`tracecat-registry-*`)
  - Workflow artifacts (`tracecat-workflow-*`)
- IAM roles/policies for ECS runtime, secrets, S3, Redis metadata access
- VPC endpoints for S3 + Secrets Manager

## Quick start

```bash
cd deployments/fargate

# 1) Initialize
terraform init

# 2) Create required core secrets (requires AWS CLI)
export AWS_DEFAULT_REGION=us-east-1
./scripts/create-aws-secrets.sh

# 2) Set required vars (example)
export TF_VAR_aws_region=us-east-1
export TF_VAR_domain_name=tracecat.example.com
export TF_VAR_hosted_zone_id=Z1234567890

export TF_VAR_tracecat_db_encryption_key_arn=arn:aws:secretsmanager:...
export TF_VAR_tracecat_service_key_arn=arn:aws:secretsmanager:...
export TF_VAR_tracecat_signing_secret_arn=arn:aws:secretsmanager:...

# 3) (Optional) pin app image tag
export TF_VAR_tracecat_image_tag=1.0.0-beta.6

# 4) Deploy
terraform apply
```

## Key outputs

After apply, useful outputs include:

- `public_app_url`
- `public_api_url`
- `s3_attachments_bucket_name`
- `s3_registry_bucket_name`
- `s3_workflow_bucket_name`
- `core_db_endpoint`
- `redis_endpoint`
