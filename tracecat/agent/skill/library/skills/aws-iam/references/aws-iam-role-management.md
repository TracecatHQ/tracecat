# IAM Role Management

## Overview

This skill provides a structured workflow for identifying, creating, and maintaining IAM roles as part of any resource provisioning or update task. Without explicit guidance, agents tend to skip role creation, produce malformed trust policies, use overly broad permissions, or miss implicit role dependencies.

When the prompt provides sufficient context (resource names, service types), proceed directly with role creation. Do not ask for confirmation or additional parameters — use the account ID and region from your AWS session context.

## Role identification

Before creating any AWS resource, determine all IAM roles the task requires:

**Service roles** — assumed by an AWS service to act on your behalf (e.g., Glue crawler reading S3, Firehose delivering to S3). The service itself is the principal.

**Execution roles** — assumed by an AWS service to run customer code (e.g., Lambda execution role, ECS task role).

For each resource:

1. Identify whether the service requires a role to operate
2. Check whether the service uses a service-linked role (no custom role needed — e.g., GuardDuty, Auto Scaling)
3. Identify dependent resources that also need roles (e.g., CodePipeline + CodeBuild)

Do not skip role creation by referencing "pre-existing roles" unless the user explicitly provides a role ARN.

## Create service role

1. Identify the correct service principal for the service that will assume the role at runtime
2. Construct the trust policy with confused deputy protections
3. Identify all resources the role needs to access from the current task context
4. Build a scoped permissions policy using those resource ARNs
5. Attach relevant managed policies where they exist

### Trust policy

1. Use the correct service principal: always `[service].amazonaws.com` (e.g., `glue.amazonaws.com`, `states.amazonaws.com`). The trust principal must be the service that actually calls `sts:AssumeRole` at runtime, which may differ from the service being configured (e.g., CloudWatch Synthetics canaries use `lambda.amazonaws.com`).
2. Include confused deputy protections — add both `aws:SourceArn` and `aws:SourceAccount` conditions:
   - When the resource name is provided, use it in `aws:SourceArn` — construct the full ARN including account ID, region, and resource type. Do not use wildcards when the name is known.
   - When the resource name is genuinely unknown, use a wildcard ARN with as much specificity as possible.
   - Include `aws:SourceAccount` with the full account ID.
   - Include `aws:SourceArn` and `aws:SourceAccount` conditions when the assuming service supports them — most major services do, including Glue, CloudTrail, Firehose, Lambda, S3 replication, DataSync, and VPC Flow Logs. Check service documentation if unsure which condition keys a specific service populates.
   - Example:

     ```json
     {
       "Version": "2012-10-17",
       "Statement": [
         {
           "Effect": "Allow",
           "Principal": { "Service": "glue.amazonaws.com" },
           "Action": "sts:AssumeRole",
           "Condition": {
             "StringEquals": { "aws:SourceAccount": "123456789012" },
             "ArnLike": {
               "aws:SourceArn": "arn:aws:glue:us-east-1:123456789012:crawler/my-crawler"
             }
           }
         }
       ]
     }
     ```

3. The trust policy goes in `AssumeRolePolicyDocument`, not in the permissions policy.

### Permissions policy

1. Identify all resources from the current task that this role will access — buckets, tables, streams, log groups, etc. Construct the most specific resource ARN possible. Use `*` only for components you genuinely don't know.
2. Scope CloudWatch Logs actions (`logs:CreateLogStream`, `logs:PutLogEvents`, `logs:DescribeLogStreams`) to the specific log group ARN, not `Resource: *`. Use the pattern `arn:aws:logs:REGION:ACCOUNT:log-group:LOG_GROUP_NAME:*`.
3. Separate permissions by purpose into distinct policy statements (e.g., source-read vs. target-write).
4. Attach AWS managed policies when they closely match the work (e.g., `AWSGlueServiceRole`). Supplement with scoped inline policies for resource-specific access.
5. Do not use `"Action": "*"` or `"Resource": "*"` as a pair. If broad access is genuinely needed, explain why.

### Naming

Use a descriptive role name identifying the service and purpose (e.g., `GlueETL-my-job`, `FirehoseDelivery-my-stream`).

## Maintain service role

When updating a resource that has an associated service role:

1. Read the existing role's trust policy, permissions policy, and tags
2. If tags indicate the role is managed by an external tool (e.g., `aws:cloudformation:stack-name`, `managed-by: terraform`), flag this to the user before proceeding
3. **If the trust policy lacks `aws:SourceArn` and `aws:SourceAccount` conditions, add them** — this is required, not optional. Follow the confused deputy guidance from the Create section. Use the specific resource ARN from the task context.
4. Update the permissions policy to cover the new activity — prefer extending the existing role over creating a new one when the trust principal is unchanged
5. If existing permissions are broader than needed after the update, offer to scope them down

## Create execution role

When creating an execution role (Lambda, ECS task, EC2 instance profile, EKS pod):

1. Include baseline permissions the execution environment needs (e.g., `AWSLambdaBasicExecutionRole` for Lambda)
2. If the user's prompt specifies what the code will do, create a scoped role matching those responsibilities. If the prompt signals exploratory/PoC intent, use broader permissions
3. Briefly explain the scoping choice and offer to adjust

## Maintain execution role

When altering code that runs in an AWS execution environment:

1. Examine the associated execution role and its tags
2. If code changes introduce new AWS API calls, verify the role permits them and update if not
3. Do not silently remove permissions — confirm with the user before narrowing

## Gotchas

- Trust policy and permissions policy are separate documents. Never put resource-scoped permissions inside the trust policy.
- Some services use service-linked roles that AWS manages automatically. Do not create custom roles for these — verify first.
- When a task involves multiple services in a chain (e.g., SES → Firehose → S3), each link may need its own role. Create separate, purpose-specific roles.

## Additional Resources

- [IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [The Confused Deputy Problem](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)
- [IAM Access Analyzer](https://docs.aws.amazon.com/IAM/latest/UserGuide/what-is-access-analyzer.html)
