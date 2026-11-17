# AWS IAM Policy Generation

## CRITICAL RULE — READ THIS FIRST

**If the user provides application source code (in any language) OR a Terraform plan JSON (`terraform show -json` output):**
You MUST propose the `uvx iam-policy-autopilot@latest generate-policies` command. You MUST NOT manually analyze the code or plan and construct a policy yourself. The Autopilot tool does deterministic static analysis — your job is to construct the correct command with the right flags, NOT to replicate what the tool does. (The Input Gate below lists the languages known to work and directs you to confirm a language's status with `generate-policies --help`.)

**If no source code or Terraform plan JSON is provided, or the language is unsupported (Rust, C#, PHP, Ruby, etc.):**
Use the Service Authorization Reference path to produce the policy directly.

**Terraform plan JSON vs native `.tf` files — CRITICAL distinction:** Autopilot accepts a Terraform plan JSON (`terraform show -json plan.tfplan`) as a direct input. Native `.tf` configuration files are NOT supported as a direct input — if the user only has `.tf` files, instruct them to produce a plan JSON first (see Task 2b). The separate `--tf-dir`/`--tfstate` flags remain available only as ARN-refinement companions to source-code analysis.

Throughout this reference, "Terraform plan JSON" is the canonical term for this input: a Terraform plan rendered to JSON via `terraform show -json`.

## Overview

Generates baseline AWS IAM identity-based policies through two complementary approaches:

1. **IAM Policy Autopilot** (primary, MANDATORY when source code in a supported language OR a Terraform plan JSON is present) — Deterministic static analysis. For source code, it produces policies by parsing actual AWS SDK calls. For a Terraform plan JSON, it maps the plan's resource changes to the AWS SDK operations the Terraform AWS provider performs. Preferred for security because it is reproducible and not subject to LLM hallucination. You MUST use this path when either input is available.
2. **Service Authorization Reference** (fallback) — Queries the programmatic service reference to map API operations to correct IAM actions. Used ONLY when Autopilot is unavailable, the task involves neither source code nor a Terraform plan JSON, or the source language is not supported.

**Output completeness rule — CRITICAL:**

- When using the Service Authorization Reference path (Task 3): You MUST always produce a complete policy document in your response. Use placeholder values (e.g., `<bucket-name>`, `<role-arn>`, `<REGION>`) when specific resource names are not provided. NEVER ask clarifying questions instead of producing a policy.
- When using the Autopilot path (Task 2 or Task 2b): You MUST produce the complete `uvx iam-policy-autopilot@latest generate-policies` command with all required flags. You MUST NOT manually construct a policy — the Autopilot command IS your output. Do not also produce a hand-written policy "in case Autopilot isn't available."
- If the user's code uses runtime variables for resource names, use descriptive placeholders in the ARNs (Reference path) or note that Autopilot handles this automatically (Autopilot path).

## Common Tasks

### 0. Input Gate (MANDATORY FIRST STEP)

Identify what the user provided before choosing a path.

**Terraform plan JSON** (use Autopilot — Task 1 → Task 2b): a plan rendered to JSON with `terraform show -json`. This input is supported regardless of any application language considerations. Native `.tf` configuration files alone do NOT qualify — the user must produce a Terraform plan JSON first (Task 2b shows how).

**Supported languages** for source code (use Autopilot — Task 1 → Task 2). This list is not exhaustive; Autopilot is the source of truth for language support, so confirm a language's status with `uvx iam-policy-autopilot@latest generate-policies --help` (or the [Supported Languages table](https://github.com/awslabs/iam-policy-autopilot#supported-languages-and-sdks-for-policy-generation)) rather than relying on this list:

- Python (boto3)
- Go (SDK v2)
- TypeScript/JavaScript (SDK v3)
- Java (SDK v2)

**Unsupported languages** (e.g., Rust, C#, PHP, Ruby, Kotlin, Swift — treat as unsupported any language not confirmed by `uvx iam-policy-autopilot@latest generate-policies --help`) — fall back silently to the Service Authorization Reference path (Task 3). Do NOT attempt to use Autopilot for a language you have confirmed is unsupported. Do NOT ask the user whether to proceed with the fallback — just produce the policy directly using the Reference path. Even if a Terraform directory is mentioned alongside unsupported-language code, you MUST NOT attempt to use `--tf-dir` with Autopilot — the language is unsupported, so Autopilot cannot be used at all. However, if the user provides (or can produce) a Terraform **plan JSON**, that is an independent supported input — use Task 2b for the plan even when application code is in an unsupported language.

**For supported languages**, you MUST:

1. Propose the `uvx iam-policy-autopilot@latest generate-policies` command with the correct flags
2. Present the command for the user to run
3. You MUST NOT use `service_reference_query`, `curl`, or any manual approach to derive policies from source code when the language is supported by Autopilot

You MUST NOT manually analyze source code and construct policies yourself when Autopilot can do it deterministically. The entire point of Autopilot is that it produces reproducible, auditable results without LLM interpretation. Your job is to construct the correct Autopilot command, not to replicate what Autopilot does.

Fall back to the Service Authorization Reference path ONLY when:

- The `iam-policy-autopilot` CLI is not installed AND installation fails
- The user's task involves neither source code nor a Terraform plan JSON (e.g., they name specific API operations or actions directly)
- The source language is not supported — treat as unsupported any language not confirmed by `uvx iam-policy-autopilot@latest generate-policies --help` (e.g., Rust, C#, PHP, Ruby, Kotlin, Swift) — and no Terraform plan JSON is available

### 1. Verify Autopilot Availability

The tool runs via `uvx` (the Python package runner from `uv`). No separate installation is needed — `uvx` downloads and executes the tool in one step.

**Constraints:**

- You MUST verify `uvx` is available before any policy generation task involving source code or a Terraform plan JSON
- You MUST NOT skip this step or assume availability

```bash
uvx iam-policy-autopilot@latest --version
```

If this fails:

- If `uvx` is not found: attempt installation before falling back. Try `brew install uv` (macOS) or `pip install uv` (any platform). If installation succeeds, retry the version check.
- If `uv` cannot be installed: try installing iam-policy-autopilot directly via `pip install iam-policy-autopilot` and then run `iam-policy-autopilot --version`.
- If ALL installation attempts fail: inform the user and fall back to the Service Authorization Reference path (Task 3).
- If `uvx` is found but the command fails for another reason (network error, etc.): retry once, then fall back.

The goal is to use Autopilot whenever possible — exhaust installation options before falling back to LLM-based policy generation.

Once `uvx iam-policy-autopilot@latest --version` (or `iam-policy-autopilot --version`) succeeds, proceed with Task 1b.

### 1b. Discover Account ID and Region

Before constructing the Autopilot command, attempt to discover the AWS account ID and region. These produce more precisely scoped resource ARNs in the generated policy (without them, Autopilot uses wildcards).

**Discovery methods (try in order):**

1. **User-provided values** — If the user specified an account ID or region in their prompt, use those directly.
2. **Environment variables** — Check for `AWS_ACCOUNT_ID`, `AWS_DEFAULT_REGION`, or `AWS_REGION`:

   ```bash
   echo "Account: ${AWS_ACCOUNT_ID:-not set}" && echo "Region: ${AWS_REGION:-${AWS_DEFAULT_REGION:-not set}}"
   ```

3. **AWS CLI / STS** — If AWS credentials are configured, query STS:

   ```bash
   aws sts get-caller-identity --query Account --output text
   aws configure get region
   ```

4. **Project configuration files** — Look for account/region in common locations:
   - `terraform.tfvars`, `*.tf` files (look for `region` or `account_id` variables)
   - `cdk.json` or `cdk.context.json`
   - `samconfig.toml` (look for `region` parameter)
   - `.env` files (look for `AWS_REGION`, `AWS_ACCOUNT_ID`)
   - `serverless.yml` (look for `provider.region`)

**Constraints:**

- You SHOULD attempt discovery but MUST NOT block on it — if discovery fails, proceed without `--account` and `--region` (Autopilot will use wildcards in ARNs)
- You MUST NOT hallucinate or guess account IDs or regions. If you cannot discover them through the methods above, OMIT the `--account` and `--region` flags entirely. A missing flag (producing wildcard ARNs) is always better than a fabricated value (producing incorrect ARNs that won't match real resources).
- You MUST NOT ask the user for their account ID or region if you can discover it automatically
- You MUST NOT output or log any values from configuration files other than account ID and region. If secrets are found alongside configuration, recommend migrating them to AWS Secrets Manager or SSM Parameter Store.
- If you discover values, include them as `--account` and `--region` flags in the Autopilot command

### 2. Generate Policies from Source Code (Autopilot)

Analyzes source files using deterministic static analysis to produce minimal IAM identity-based policies.

**When to use:** User has application source code that makes AWS SDK calls and wants IAM policies generated from it.

```bash
uvx iam-policy-autopilot@latest generate-policies \
  /home/user/project/src/app.py /home/user/project/src/handler.py \
  --region us-east-1 \
  --account 123456789012 \
  --service-hints s3 dynamodb \
  --pretty
```

**Required parameters:**

- `<source_files>` — One or more absolute paths to source files

**Optional parameters:**

- `--region <REGION>` — AWS region for resource ARNs
- `--account <ACCOUNT>` — AWS account ID for resource ARNs
- `--service-hints <SERVICES>` — Space-separated AWS service names to scope analysis
- `--pretty` — Pretty-print JSON output
- `--upload-policies <PREFIX>` — Upload generated policies to IAM with given prefix
- `--tf-dir <DIR>` — Terraform project directory for more precise ARNs
- `--tfstate <FILES>` — terraform.tfstate files for deployed resource ARNs (highest precision)
- `--explain <PATTERN>` — Explain why specific actions were included

**Constraints:**

- You MUST use absolute paths when passing source files
- You MUST include ALL relevant source files that interact with AWS services
- You MUST ONLY include files that contain runtime AWS SDK calls — do NOT include infrastructure-as-code files (CDK stacks, Terraform configs, CloudFormation templates) as these define resources, not runtime behavior
- You SHOULD use `--service-hints` to reduce false positives from ambiguous method names
- You MUST include `--region` and `--account` if values were discovered in Task 1b or provided by the user — these produce scoped ARNs instead of wildcards
- You MUST NOT upload or apply policies without explicit user confirmation
- When the user confirms use of `--upload-policies`, recommend enabling CloudTrail logging and CloudWatch alarms for IAM changes (see Security Considerations)
- You MUST NOT use `service_reference_query` or manually construct the policy — delegate to Autopilot
- You MUST NOT call AWS APIs or query the service authorization reference as a substitute for running Autopilot
- The presence of non-AWS libraries (HTTP clients, database drivers, Redis, etc.) in the same file does NOT disqualify Autopilot — it only analyzes AWS SDK calls and ignores everything else

**Terraform integration — MANDATORY:**

- If the user mentions a Terraform directory, Terraform project, or Terraform state, you MUST include `--tf-dir <absolute_path>` (or `--tfstate <file>`) in the Autopilot command. This is NOT optional.
- You MUST NOT manually construct a policy when both source code in a supported language AND a Terraform directory are available — Autopilot with `--tf-dir` produces more precise ARNs than manual construction.
- If the user wants a policy for the permissions Terraform itself needs to apply a plan (rather than for application runtime code), use Task 2b with the plan JSON as the input instead.

### 2b. Generate Policies from a Terraform Plan (Autopilot)

Maps a Terraform plan's resource changes to the AWS SDK operations the Terraform AWS provider performs, producing the IAM policy needed to apply the plan.

**When to use:** User has a Terraform plan JSON (or a Terraform project they can plan) and wants the IAM policy required for the changes it describes — e.g., a baseline policy for a CI/CD deployment role that runs `terraform apply`.

**Input requirements — CRITICAL:**

- The input MUST be a Terraform plan JSON produced by `terraform show -json`. Native `.tf` configuration files are NOT supported as a direct input.
- If the user only has `.tf` files, provide these commands to produce the plan JSON first:

```bash
umask 077                                      # new files are created owner-only (0600)
terraform plan -out=plan.tfplan               # write the (binary) plan
terraform show -json plan.tfplan > plan.json  # render it to JSON
chmod 600 plan.tfplan plan.json               # ensure owner-only if umask was already looser
```

**Sensitive data warning — both the binary plan and the plan JSON may contain secrets:** A Terraform plan embeds planned resource attribute values, which can include database passwords, API keys, connection strings, and other secrets. This applies to BOTH the binary `plan.tfplan` and its `plan.json` rendering. Treat both files with the same care as a credential file: restrict their permissions to owner-only (the `umask 077` / `chmod 600` above), keep them on encrypted storage (e.g., an encrypted home volume or an encrypted EBS volume on a build host), do NOT print or log their contents, do NOT commit them to source control, and delete them once the policy is generated. You MUST NOT echo the plan contents into your response. Autopilot reads only the resource change actions, not secret values, so nothing sensitive needs to be surfaced to the user.

Then pass the plan JSON in place of source files:

```bash
uvx iam-policy-autopilot@latest generate-policies \
  /home/user/project/plan.json \
  --region us-east-1 \
  --account 123456789012 \
  --pretty

rm -f plan.tfplan plan.json                    # delete the sensitive plan files once done
```

**Constraints:**

- You MUST use an absolute path to the plan JSON file
- The input kind (source code vs Terraform plan) is detected automatically — you MUST NOT mix source files and a plan JSON in a single invocation. If the user needs both an application runtime policy and a Terraform deployment policy, run two separate invocations.
- You MUST include `--region` and `--account` if values were discovered in Task 1b or provided by the user
- You MUST NOT manually map Terraform resources to IAM actions yourself — delegate to Autopilot
- You MUST NOT upload or apply policies without explicit user confirmation
- The `--tf-dir` and `--tfstate` flags do not apply to this path — they refine ARNs during source-code analysis. When the plan JSON is the input, precision comes from the plan itself.
- When the generated policy is for a CI/CD role that runs `terraform apply`, recommend the pipeline assume the role via short-lived federated credentials (e.g., GitHub Actions or GitLab CI OIDC, or an instance profile / IAM Roles Anywhere) rather than long-lived IAM user access keys. Ephemeral credentials that rotate automatically are strongly preferred for deployment roles.
  - Scope the role's trust policy so only the intended pipeline can assume it. For GitHub Actions OIDC, restrict `token.actions.githubusercontent.com:sub` to specific `repo:<org>/<repo>:ref:<branch>` (or environment) values and pin `:aud` to `sts.amazonaws.com`; for GitLab CI OIDC, restrict the equivalent `:sub`/`:aud` claims. Without a `sub` condition, any workflow (or any repo in the org) that reaches the OIDC provider could assume the role.
  - A role that can run `terraform apply` can create, modify, and delete infrastructure, so recommend enabling CloudTrail logging of its activity and CloudWatch alarms for unexpected or privilege-escalating actions (e.g., `iam:*`, `CreatePolicyVersion`, `AttachRolePolicy`). Encrypt the CloudTrail S3 bucket with SSE-KMS (and enable log file validation), encrypt any CloudWatch Logs log groups that receive these events with a KMS key, and encrypt the SNS topic used for alarm notifications (and confirm its subscribers are authorized personnel), since deployment activity can reference sensitive resource names.
  - Any secrets the pipeline needs (Terraform variables holding passwords, API keys, or connection strings referenced by the plan) should be sourced from AWS Secrets Manager or SSM Parameter Store at apply time rather than hardcoded in `.tf` files, `.tfvars`, or CI environment variables committed to source control.

### 3. Generate Policies from API Operations (Service Authorization Reference)

**When to use:** Autopilot is unavailable, the task involves neither source code nor a Terraform plan JSON, or the user names specific API operations/IAM actions directly.

#### 3a. Verify Dependencies

**Constraints:**

- You MUST check whether the `service_reference_query` tool is available
- If unavailable, proceed with the `curl` and `jq` fallback automatically — do NOT ask the user for permission to proceed

#### 3b. Gather Parameters

Collect the information needed to generate the policy.

**Required parameters:**

- `operations` — The AWS API operations the user wants to perform (e.g., `CopyObject` — note: this is an API operation, not an IAM action. CopyObject requires `s3:GetObject` + `s3:PutObject`; there is no `s3:CopyObject` IAM action). API operation names and IAM action names frequently differ.

**Optional parameters:**

- `account_id` — AWS account ID for ARN construction (default: placeholder `123456789012`)
- `region` — AWS region (default: `us-east-1`)
- `resource_scope` — Specific resource ARNs or patterns (default: derived from service reference)
- `policy_type` — `identity` or `resource` (default: `identity`)

**Constraints:**

- You MUST ask for all required parameters upfront in a single prompt if they are not already provided in the user's request
- You MUST support multiple input methods (direct input, file path, URL)
- You MUST confirm the interpreted operations with the user before proceeding ONLY if the request is ambiguous — if the operations are clear from context, proceed directly

#### 3c. Query the Service Authorization Reference

Look up the correct IAM actions for each requested API operation.

The reference lives at `https://servicereference.us-east-1.amazonaws.com/v1/<service>/<service>.json`. These files are large. Use the `service_reference_query` tool or `curl` with `jq` to extract only what you need.

See [service authorization reference details](service-authorization.md) for all query patterns and the reference structure.

**Tool call example:**

```
service_reference_query(service="lambda", operation="CreateFunction")
```

**CLI fallback** (when the tool is unavailable):

```bash
curl -s "https://servicereference.us-east-1.amazonaws.com/v1/lambda/lambda.json" | \
  jq '.Operations[] | select(.Name == "CreateFunction")'
```

**Constraints:**

- You MUST query the service authorization reference for every operation — never assume action names
- You MUST include ALL actions listed in `AuthorizedActions` for each operation, including cross-service actions (e.g., `iam:PassRole` for `lambda:CreateFunction`) and prerequisite actions (e.g., `lambda:GetLayerVersion` for `lambda:CreateFunction` — required to attach layers during creation). Do NOT omit actions from the AuthorizedActions list based on your own judgment about whether they seem "optional" — if the service reference lists them, include them.
- You MUST NOT include actions for optional service variants (e.g., `s3-object-lambda:*`, `s3:GetObjectVersion`, `s3:GetObjectTagging`) unless the user explicitly mentions Object Lambda, versioning, tagging, access points, or similar features
- You MUST NOT use the API operation name as the IAM action unless the reference confirms they match
- You MUST NOT add actions for operations the user did not request — the policy must cover exactly what was asked
- If the user names a specific IAM action directly (e.g., "allow s3:PutObject"), you MUST use that exact action without expanding it to all authorized actions for the underlying API operation
- If the user names a specific condition key (e.g., "use aws:TagKeys"), you MUST use that exact key — do not substitute a service-specific alternative
- You SHOULD explain to the user what you are querying and why

#### 3d. Construct the Policy

Build the IAM policy document from the queried actions.

**Pre-flight check — BEFORE writing any action name into a policy, verify it is not in the hallucinated-actions table (see Troubleshooting section).** Common mistakes: writing `s3:SelectObjectContent` instead of `s3:GetObject`, `s3:HeadObject` instead of `s3:GetObject`, `s3:CreateMultipartUpload` instead of `s3:PutObject`, `s3:DeleteBucketEncryption` instead of `s3:PutEncryptionConfiguration`. If you are about to write any S3 action that looks like an API operation name rather than a permission name, STOP and check the table.

**Constraints:**

- You MUST scope resources using specific ARNs when possible — avoid `*`
- You MUST separate cross-service actions (e.g., `iam:PassRole`) into their own statement with appropriate conditions
- You MUST present the complete policy to the user and explain each statement before considering the task complete
- You MUST NOT include "optional", "additional", or "you may also need" permissions sections in your response. If the user asked for permission to create an API, provide ONLY the creation permission. Do not suggest read, update, or delete permissions "in case they need them later." This over-grants permissions even when labeled as optional.
- Your response MUST contain exactly ONE policy document. Do not present a "minimal" policy followed by a "comprehensive" or "expanded" policy — only the minimal one. If the user needs more permissions, they will ask.
- You MUST NOT add actions for operations the user did not request — the policy must cover exactly what was asked, nothing more

**Resource-based policy requirements:**

When constructing resource-based policies (i.e., `policy_type` is `resource`), you MUST include condition keys to prevent confused deputy attacks where applicable:

- `aws:SourceArn` — to restrict which resource ARN can invoke the cross-service call
- `aws:SourceAccount` — to restrict which account ID can make the request
- `aws:PrincipalOrgID` — to restrict access to principals within a specific AWS Organization

Include whichever condition keys are supported by the service and relevant to the use case. Omit only when the service does not support the key or the user explicitly requests unrestricted access.

**Condition operator safety rules (CRITICAL):**

- When using `ForAnyValue` in a **Deny** statement, you MUST add a separate Deny statement with a `Null` condition (`"Null": {"<key>": "true"}`) to handle the case where the context key is absent. Without this, requests missing the key bypass the deny entirely.
- When using `ForAllValues` in an **Allow** statement, you MUST add a `Null` condition (`"Null": {"<key>": "false"}`) in the same statement to require the key to exist. Without this, requests missing the key are silently allowed.
- `ForAnyValue` and `ForAllValues` MUST only be used with array-typed condition keys (`ArrayOfString`, `ArrayOfARN`, etc.) — never with scalar types.
- Multi-valued condition keys (e.g., `aws:TagKeys`, `aws:VpceOrgPaths`) MUST use a set operator (`ForAnyValue:` or `ForAllValues:`) — plain `StringNotLike` or `StringEquals` without a set operator is INCORRECT for these keys.

**Worked example — ForAnyValue:StringNotLike in Deny (MANDATORY pattern):**

When restricting access based on a multi-valued key like `aws:VpceOrgPaths`, you MUST produce TWO Deny statements:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyNonMatchingVpceOrgPath",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": ["arn:aws:s3:::my-bucket", "arn:aws:s3:::my-bucket/*"],
      "Condition": {
        "ForAnyValue:StringNotLike": {
          "aws:VpceOrgPaths": "o-orgid/r-rootid/ou-ouid/*"
        }
      }
    },
    {
      "Sid": "DenyMissingVpceOrgPath",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": ["arn:aws:s3:::my-bucket", "arn:aws:s3:::my-bucket/*"],
      "Condition": {
        "Null": { "aws:VpceOrgPaths": "true" }
      }
    }
  ]
}
```

Key rules for this pattern:

1. Use `ForAnyValue:StringNotLike` (NOT plain `StringNotLike`) because `aws:VpceOrgPaths` is a multi-valued/array key
2. The `Null` check MUST reference the SAME condition key (`aws:VpceOrgPaths`), not a different key like `aws:VpcEndpointId`
3. Without the Null statement, requests not traversing any VPC endpoint bypass the deny entirely

See [common pitfalls](common-pitfalls.md) for additional examples.

## Decision Guide

| Situation                                            | Path      | Command/Approach                                   |
| ---------------------------------------------------- | --------- | -------------------------------------------------- |
| Source code using AWS SDKs                           | Autopilot | `generate-policies` with source files              |
| Terraform plan JSON (`terraform show -json` output)  | Autopilot | `generate-policies` with the plan JSON file        |
| Only native `.tf` files, want a deployment policy    | Autopilot | `terraform plan` + `terraform show -json`, then 2b |
| Policy seems too broad from Autopilot                | Autopilot | Re-run with `--service-hints`                      |
| Need to understand a specific action                 | Autopilot | Use `--explain` with an action pattern             |
| Source code + Terraform project, want precise ARNs   | Autopilot | Add `--tf-dir` or `--tfstate` flags                |
| Autopilot unavailable or install failed              | Reference | Query service authorization reference              |
| User names specific API operations (no code or plan) | Reference | Query service authorization reference              |
| Unsupported language (and no Terraform plan JSON)    | Reference | Query service authorization reference              |
| Need resource-based policies                         | Reference | Autopilot only supports identity-based             |

## Security Considerations

- **Over-permissive policies:** If `--service-hints` are omitted, Autopilot may match ambiguous method names across multiple services, producing broader policies than intended. When using the Reference path, incomplete operation lists or missing cross-service actions can result in either over- or under-permissive policies. Always review generated policies before deployment.
- **Credential exposure during discovery:** Task 1b queries STS and reads project configuration files (`.env`, `terraform.tfvars`) to discover account IDs and regions. Ensure these files do not contain secrets beyond what is needed, and be aware that STS calls appear in CloudTrail logs.
- **Policy upload without approval:** The `--upload-policies` flag creates and attaches IAM policies directly. You MUST NOT use this flag without explicit user confirmation. When using `--upload-policies`, recommend that users:
  - Enable CloudTrail logging to audit IAM policy creation and attachment events
  - Enable SSE-KMS encryption on the CloudTrail S3 bucket and enable log file validation
  - Set up CloudWatch alarms for unexpected IAM changes (e.g., `CreatePolicy`, `AttachRolePolicy` events)
  - Encrypt CloudWatch Logs log groups that receive IAM change events using a KMS key
  - Use a change management or approval workflow before uploading to production accounts
- **Review before attaching:** Always recommend that users review generated policies before attaching them to any principal. Use `iam:SimulateCustomPolicy` or the IAM Policy Simulator to validate that the policy grants only the intended access.
- **Prefer IAM roles over IAM users:** Generated policies should preferably be attached to IAM roles for workloads (EC2 instance profiles, Lambda execution roles, ECS task roles, EKS pod identity) rather than IAM users with long-lived static access keys. Roles provide ephemeral credentials that automatically rotate.
- **Confused deputy prevention for resource-based policies:** When generating resource-based policies via the Reference path, always include condition keys to prevent confused deputy attacks:
  - `aws:SourceArn` — restricts access to a specific resource ARN making the cross-service call
  - `aws:SourceAccount` — restricts access to a specific account ID
  - `aws:PrincipalOrgID` — restricts access to principals within a specific AWS Organization
  - Include whichever keys are applicable based on the service and use case

## Troubleshooting

### Autopilot not found

If `uvx` is not installed, the user needs to install `uv` first: https://docs.astral.sh/uv/getting-started/installation/ (or `brew install uv` on macOS, `pip install uv` elsewhere). Once `uv` is installed, `uvx` is available and no further setup is needed. If `uvx` cannot be installed, fall back to the Service Authorization Reference path.

### Overly broad policies from Autopilot

Use `--service-hints` to restrict analysis. Without hints, ambiguous method names may match multiple AWS services.

### No actions generated by Autopilot

Ensure source files contain actual AWS SDK client calls (e.g., `s3_client.get_object()`, `new S3Client().send()`). Wrapper functions without direct SDK usage won't be detected.

### Action name does not match API operation (Reference path)

API names and IAM actions frequently differ. Query the service authorization reference — do not guess. For example, `dynamodb:BatchExecuteStatement` does not exist as an IAM action — the operation requires `dynamodb:PartiQLDelete`, `PartiQLInsert`, `PartiQLSelect`, and `PartiQLUpdate`.

### Common hallucinated IAM actions (DO NOT USE)

These are API operation names that models incorrectly use as IAM actions. The left column shows what you MUST NOT write; the right column shows what you MUST write instead:

| ❌ WRONG (not a real IAM action) | ✅ CORRECT IAM action(s)                               |
| -------------------------------- | ------------------------------------------------------ |
| `s3:UploadPartCopy`              | `s3:PutObject` (destination) + `s3:GetObject` (source) |
| `s3:CopyObject`                  | `s3:PutObject` (destination) + `s3:GetObject` (source) |
| `s3:SelectObjectContent`         | `s3:GetObject`                                         |
| `s3:HeadObject`                  | `s3:GetObject`                                         |
| `s3:HeadBucket`                  | `s3:ListBucket`                                        |
| `s3:ListBuckets`                 | `s3:ListAllMyBuckets`                                  |
| `s3:ListObjectVersions`          | `s3:ListBucketVersions`                                |
| `s3:DeleteBucketEncryption`      | `s3:PutEncryptionConfiguration`                        |
| `s3:GetObjectLockConfiguration`  | `s3:GetBucketObjectLockConfiguration`                  |
| `s3:CreateMultipartUpload`       | `s3:PutObject`                                         |
| `dynamodb:BatchExecuteStatement` | `dynamodb:PartiQL*` actions                            |
| `apigateway:CreateRestApi`       | `apigateway:POST` + `apigateway:PUT` on `/restapis`    |
| `apigateway:CreateApi`           | `apigateway:POST` on `/apis`                           |
| `apigatewayv2:CreateApi`         | `apigateway:POST` on `/apis`                           |
| `apigateway:UpdateStage`         | `apigateway:PATCH` on `/restapis/*/stages/*`           |
| `apigateway:DeleteRestApi`       | `apigateway:DELETE` on `/restapis/<api-id>`            |

**How to read this table:** If you find yourself about to write an action from the left column, STOP and use the right column instead. The left column contains API operation names that do NOT exist as IAM actions.

When in doubt, ALWAYS query the service authorization reference. Never guess action names from API operation names.

### API Gateway resource ARN patterns

API Gateway uses HTTP-verb-based actions (POST, GET, PUT, PATCH, DELETE). Always scope to the specific resource path — do NOT use `"Resource": "*"`. This table is an illustrative cache for commonly hallucinated patterns — verify against the service authorization reference for current mappings:

| Operation            | Action(s)                           | Resource ARN                                    |
| -------------------- | ----------------------------------- | ----------------------------------------------- |
| Create REST API      | `apigateway:POST`, `apigateway:PUT` | `arn:aws:apigateway:*::/restapis`               |
| Create HTTP API (v2) | `apigateway:POST`                   | `arn:aws:apigateway:*::/apis`                   |
| Create authorizer    | `apigateway:POST`                   | `arn:aws:apigateway:*::/restapis/*/authorizers` |
| Create domain name   | `apigateway:POST`                   | `arn:aws:apigateway:*::/domainnames`            |
| Update stage         | `apigateway:PATCH`                  | `arn:aws:apigateway:*::/restapis/*/stages/*`    |
| Delete REST API      | `apigateway:DELETE`                 | `arn:aws:apigateway:*::/restapis/<api-id>`      |
| Invoke (data plane)  | `execute-api:Invoke`                | `arn:aws:execute-api:*:*:<api-id>/<stage>/*/*`  |

**IMPORTANT — API Gateway v2 (HTTP APIs) ARN format:**

- HTTP APIs (v2) use `/apis` in the IAM resource ARN — NOT `/v2/apis`
- The `/v2/` prefix is an API endpoint URL path, NOT part of the IAM ARN format
- Both REST APIs (`/restapis`) and HTTP APIs (`/apis`) use the same `apigateway:` service prefix in IAM
- Do NOT confuse the AWS CLI/SDK endpoint path with the IAM resource ARN

**IMPORTANT — CreateRestApi requires both POST and PUT:**

- The `CreateRestApi` operation requires `apigateway:POST` for the core creation, plus `apigateway:PUT` for import/clone operations that occur during creation (e.g., importing an OpenAPI definition)
- Always include both `apigateway:POST` and `apigateway:PUT` when generating policies for REST API creation

### Missing cross-service actions (Reference path)

Some operations require actions in other services (e.g., `lambda:CreateFunction` requires `iam:PassRole`). Always check the full `AuthorizedActions` list including entries where `Service` differs from the queried service.

**Lambda CreateFunction — commonly incomplete action list (verify against service reference):**
The `CreateFunction` operation requires ALL of the following:

- `lambda:CreateFunction` (core action)
- `lambda:GetLayerVersion` (required to attach layers during creation)
- `lambda:TagResource` (required if tags are applied at creation)
- `iam:PassRole` with `iam:PassedToService` condition for `lambda.amazonaws.com` (cross-service, separate statement)

Do NOT omit `lambda:GetLayerVersion` — it is listed in `AuthorizedActions` and is required for the operation to succeed when layers are involved.

### ForAnyValue/ForAllValues behaving unexpectedly

These operators have critical edge cases with missing context keys. See [common pitfalls](common-pitfalls.md) for the Null-check patterns required to use them safely.

### Access denied despite correct action (Reference path)

Verify the resource ARN format matches what the service expects. Use query pattern 3 from the [service authorization reference](service-authorization.md) to look up the correct ARN format.

## Supported Inputs (Autopilot)

| Language   | SDK                       |
| ---------- | ------------------------- |
| Python     | boto3, botocore           |
| Go         | AWS SDK for Go v2         |
| TypeScript | AWS SDK for JavaScript v3 |
| JavaScript | AWS SDK for JavaScript v3 |
| Java       | AWS SDK for Java v2       |

This table is not exhaustive; Autopilot is the source of truth for language and SDK support. Confirm a language's status with `uvx iam-policy-autopilot@latest generate-policies --help` or the [Supported Languages table](https://github.com/awslabs/iam-policy-autopilot#supported-languages-and-sdks-for-policy-generation) rather than relying on this table.

In addition to source code, Autopilot accepts a **Terraform plan JSON** (`terraform show -json` output) as a direct input — see Task 2b. Native `.tf` configuration files are not supported as a direct input.

## Scope and Limitations

- Autopilot produces IAM **identity-based policies** only
- Autopilot does NOT support resource-based policies, RCPs, SCPs, or permission boundaries — use the Reference path for these
- Runtime-determined resource names cannot be predicted by Autopilot — use `--tfstate` for deployed resource ARNs
- Terraform plans MUST be rendered to JSON (`terraform show -json`) before use — native `.tf` configuration files are not supported as a direct input
- Source code and a Terraform plan JSON cannot be mixed in a single invocation — run separately for each
- A Terraform plan JSON may contain sensitive planned attribute values (passwords, API keys, connection strings). Handle it like a credential file: restrict access, never log or echo its contents, keep it out of source control, and delete it after policy generation
- The Reference path can construct both identity and resource-based policies

## Additional Resources

- [IAM Policy Autopilot GitHub](https://github.com/awslabs/iam-policy-autopilot)
- [Supported Languages and SDKs](https://github.com/awslabs/iam-policy-autopilot#supported-languages-and-sdks-for-policy-generation)
- [IAM Actions, Resources, and Condition Keys](https://docs.aws.amazon.com/service-authorization/latest/reference/)
- [IAM Policy Evaluation Logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)
- [IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [Common pitfalls with condition operators](common-pitfalls.md)
- [Service authorization reference query patterns](service-authorization.md)
