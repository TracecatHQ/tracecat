# Connect Tracecat to AWS

Follow the [connection guide](https://docs.tracecat.com/cheatsheets/connection-guides/connect-to-aws) to get your workspace's Principal ARN and External ID, prepare AWS access, and verify the connection. Keep this directory together. The Terraform example loads the CloudFormation templates from this directory.

## Choose a deployment method

The examples create an entry role in the security account. With organization settings, they also deploy an audit role to an explicit member account list and, optionally, to the management account. GuardDuty triage writes and S3 object access are opt-in.

### CloudFormation

1. In the security account, create a regular stack from [`tracecat-secops-access-role.yaml`](cloudformation/tracecat-secops-access-role.yaml). Supply `TracecatPrincipalArn` and `ExternalId`. For organization access, supply the target audit role ARNs as a comma-separated `TargetRoleArns` value, using `arn:aws:iam::<ACCOUNT_ID>:role/TracecatSecOpsAuditRole` for each approved account. Acknowledge named IAM resources and copy the `RoleArn` output.
2. For member accounts, sign in to the StackSets delegated administrator account. Create a service-managed StackSet using [`tracecat-secops-audit-role.yaml`](cloudformation/tracecat-secops-audit-role.yaml) and set `EntryRoleArns` to the entry role ARN. Choose **Deploy to organizational units (OUs)**, enter the root or OU ID, then pick **Intersection** and enter the approved member account IDs. Select one Region and turn automatic deployment off. Verify the targets before creating the StackSet.
3. For management account reads, create a regular stack from the same audit role template in the management account with the same `EntryRoleArns`.

Set `EnableGuardDutyTriage` to `true` on the entry stack only if you want Tracecat to archive or unarchive findings and submit feedback.

### Terraform

1. Configure the `aws-security` AWS CLI profile. For organization access, also configure `aws-stacksets` for the StackSets delegated administrator and `aws-management` if deploying a management account role. Run `aws sts get-caller-identity --profile <PROFILE>` with each profile you use.
2. In [`terraform/`](terraform), create `terraform.tfvars` with the following settings. Replace the placeholders. Omit the entire `organization` block for a single account. Omit `management_account_id` if you are skipping management account reads.

   ```hcl
   region                 = "us-east-1"
   tracecat_principal_arn = "<WORKSPACE_PRINCIPAL_ARN>"
   enable_guardduty_triage = false

   organization = {
     target_ou_id          = "<ROOT_OR_OU_ID>"
     member_account_ids    = ["<APPROVED_MEMBER_ACCOUNT_ID>"]
     management_account_id = "<MANAGEMENT_ACCOUNT_ID>"
   }
   ```

3. Supply `TF_VAR_external_id` via your secret manager or CI secret injection, or let Terraform prompt for `external_id`. Restrict access to Terraform state and any saved plans because they can contain deployment inputs.
4. Run `terraform init`, then `terraform plan`. Confirm the accounts and IAM changes. Run `terraform apply` and get the ARN with `terraform output -raw role_arn`.

## Add log archive access

Use a Tracecat role dedicated to log archive access for S3. The explicit denies apply to the role's `s3:ListBucket` and `s3:GetObject` permissions across all policies. Deploy one S3 policy stack per role and put all its approved buckets in that stack's allowlist.

In the log bucket owner's account, create a regular stack from [`tracecat-secops-log-archive-s3-policy.yaml`](cloudformation/tracecat-secops-log-archive-s3-policy.yaml). Use your entry role name if the buckets are in the entry account. For a separate log archive account, include it in your approved member accounts and use `TracecatSecOpsAuditRole`. Enter the approved bucket ARNs as a comma-separated `LogBucketArns` value such as `arn:aws:s3:::example-security-logs,arn:aws:s3:::example-network-logs`. Acknowledge named IAM resources.

For SSE-KMS logs, create a separate stack from [`tracecat-secops-log-archive-kms-policy.yaml`](cloudformation/tracecat-secops-log-archive-kms-policy.yaml) for each key. Set `KeyArn` to the full KMS key ARN, `LogBucketArns` to the approved buckets encrypted by that key, and `RoleName` to the same role name. Confirm the [key policy permits decryption](https://docs.tracecat.com/cheatsheets/connection-guides/connect-to-aws#grant-read-access-to-archived-logs).

Use the entry role ARN for your Tracecat credentials and verify the entry, audit roles, and log access in Tracecat.
