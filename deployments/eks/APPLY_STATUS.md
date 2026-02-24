# Terraform Apply Status - 2026-02-24

## Background
Fixing drift between Terraform state and deployed EKS cluster in us-west-2.
Original plan: **17 to add, 9 to change, 6 to destroy**.

## Current Status: COMPLETE (+ Temporal Archival pending apply)
- **Terraform apply succeeded** — All drift-fix resources applied, zero errors
- **Helm revision 34**: `deployed`, chart `tracecat-0.3.25`, app `1.0.0-beta.17`
- **All app pods healthy** — API (2), Worker (4), Executor (4), Agent Executor (2), UI (2), Temporal (all)
- **All ExternalSecrets synced** — core-secrets, postgres-secrets, redis-secrets all `SecretSynced` / `Ready`
- **Postgres secret stable** — `deletionPolicy: Retain` in Helm chart, Secret exists and syncing
- **Temporal schema job 34**: Completed successfully (RDS access via SecurityGroupPolicy label)
- **EKS auth mode**: Flipped to `API` (intermediate `API_AND_CONFIG_MAP` no longer needed)

## Temporal Archival (pending apply)

### Summary
Added S3-based workflow archival for self-hosted Temporal. All resources are conditional on `temporal_mode == "self-hosted"` — cloud deployments are unaffected.

### Changes (6 files, +254 lines)

| File | Change |
|------|--------|
| `modules/eks/main.tf` | Added `s3_temporal_archival_bucket` local |
| `modules/eks/s3.tf` | New conditional S3 bucket with versioning, SSE, public access block, bucket policy |
| `modules/eks/iam.tf` | New `temporal_s3` IRSA role + policy for `tracecat-temporal` service account |
| `modules/eks/helm.tf` | Temporal service account config, archival provider (s3store), namespace defaults (history + visibility URIs) |
| `modules/eks/outputs.tf` | New `s3_temporal_archival_bucket` output, added to `encryption_at_rest` |
| `deployments/helm/tracecat/values.yaml` | Updated archival comment (remains commented out; Terraform overrides via `set` blocks) |

### What gets created
- **S3 bucket**: `tracecat-temporal-archival-<suffix>` with versioning, AES256 encryption, public access blocked
- **IAM role**: `tracecat-eks-temporal-s3-role` (IRSA for `tracecat-temporal` SA) with GetObject, PutObject, ListBucket
- **Kubernetes service account**: `tracecat-temporal` with IRSA annotation (replaces `default` SA on Temporal server pods)
- **Temporal server config**: Archival enabled for both history and visibility, S3 provider with correct region
- **Namespace defaults**: `default` namespace gets archival URIs (`s3://bucket/temporal-history`, `s3://bucket/temporal-visibility`)

### Terraform plan
```
8 to add, 2 to change, 0 to destroy
```
- **Add**: S3 bucket (5 resources), IAM role + policy (2), temporal DB job (re-create)
- **Change**: Helm release (archival config), RDS (in-flight scaling from previous apply)

### Deployment
```bash
env -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN terraform apply -auto-approve
```

### Verification
```bash
# 1. Verify S3 bucket exists
env -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN aws s3 ls | grep temporal-archival

# 2. Verify Temporal server uses new service account
env -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN kubectl get pod -n tracecat \
  -l app.kubernetes.io/component=frontend \
  -o jsonpath='{.items[0].spec.serviceAccountName}'
# Expected: tracecat-temporal

# 3. Verify archival in Temporal server configmap
env -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN kubectl get configmap \
  tracecat-temporal-config -n tracecat -o yaml | grep -A 20 archival

# 4. Verify namespace archival state (after setup job completes)
env -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN kubectl exec -n tracecat \
  deploy/tracecat-temporal-admintools -- temporal operator namespace describe default
# Expected: HistoryArchivalState=enabled, VisibilityArchivalState=enabled

# 5. Verify all pods healthy
env -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN kubectl get pods -n tracecat

# 6. Verify zero drift
env -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN terraform plan -detailed-exitcode
```

### Cloud mode safety
All archival resources use `count = var.temporal_mode == "self-hosted" ? 1 : 0` and dynamic `set` blocks with `for_each = var.temporal_mode == "self-hosted" ? [1] : []`. Cloud deployments (`temporal_mode = "cloud"`) skip all archival infrastructure entirely.

---

## Previous Infrastructure Changes (all applied)
- **ElastiCache**: Scaled from 1x cache.t3.micro → 3x cache.t4g.medium, Multi-AZ + auto-failover enabled
- **EKS Node Group**: Replaced t4g.2xlarge → m7g.2xlarge, scaled from 5 → 10 nodes on-demand
- **Spot Node Group**: Created (2x spot nodes, m7g/m6g/c7g.2xlarge)
- **EKS Cluster**: Auth mode CONFIG_MAP → API (via intermediate API_AND_CONFIG_MAP)
- **Security Groups**: Removed VPC CIDR fallback rules on RDS + ElastiCache SGs
- **DNS SG Rules**: 4 new cluster DNS rules for pod SGs (TCP+UDP for postgres+redis)
- **WAF**: Created WAF Web ACL + regex pattern set for attachments endpoint
- **ExternalDNS**: IAM role + policy + Helm chart deployed (replaces null_resource.dns_record)
- **IAM**: S3 role policy updated with SecretsManager access for RDS credentials
- **OIDC Provider**: Thumbprint updated
- **RDS Password Rotation**: Created (365 day schedule)
- **null_resources**: 5 destroyed (replaced by kubernetes_manifest and kubernetes_job resources)
- **ClusterSecretStore**: Imported + updated
- **Postgres ExternalSecret**: Migrated from Terraform to Helm chart with `deletionPolicy: Retain`
- **Temporal DB Job**: kubernetes_job_v1.create_temporal_databases created
- **RDS**: Updated (apply_immediately — modifications accepted and applied)
- **Image tag**: Updated to `1.0.0-beta.17`

## Remaining Cleanup
1. **Delete stale schema job**: `kubectl delete job -n tracecat tracecat-temporal-schema-31`

## Important Notes
- **AWS credentials**: Must use `env -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN` prefix — stale session token in shell
- **Helm at revision 34** (previous: 33 failed due to bool label, 32 failed rollback)
- **API startup**: The API shows a startup error on first boot (`organization_settings` FK violation for nil org UUID). This is an app-level bug in beta.17 startup, but the pod recovers after retry.

## Learnings

### 1. Postgres ExternalSecret Doom Loop (Critical)
**Problem**: `tracecat-postgres-credentials` Secret kept being deleted during Helm upgrades, causing `CreateContainerConfigError` on all pods.

**Root cause chain**:
1. Old Helm chart (0.3.2) managed the postgres ExternalSecret
2. New config sets `externalSecrets.postgres.enabled = false` (Terraform manages it)
3. Helm upgrade sees ExternalSecret was in old release but not new → deletes it
4. ESO's default `deletionPolicy: Delete` → deletes the target Secret too
5. Pods referencing the Secret fail immediately
6. With `atomic = true`, Helm rolls back → but rollback also fails → doom loop

**Final fix**: Keep postgres ExternalSecret in Helm (don't migrate to Terraform). Add `deletionPolicy: Retain` in the chart template so the Secret survives even if the ExternalSecret is deleted. Stripping Helm annotations alone does NOT work — Helm tracks resources internally in release secrets.

**Interim fixes** (to break the doom loop while diagnosing):
- `atomic = false` on Helm release to prevent rollback cascade
- Create Secret directly via kubectl as immediate unblock
- Strip Helm annotations from ExternalSecret (insufficient alone)

**Prevention**: Don't split ExternalSecret ownership between Helm and Terraform. If Helm ever managed a resource, either keep it in Helm or ensure `deletionPolicy: Retain` is set BEFORE migration.

### 2. Temporal Schema Job SecurityGroupPolicy
**Problem**: Schema setup jobs couldn't reach RDS — connection timeout.

**Root cause**: The SecurityGroupPolicy requires `tracecat.com/access-postgres: "true"` label. The temporal server pods had it but schema jobs did not.

**Fix**: Added `temporal.schema.podLabels` with `type = "string"` (Helm parses `"true"` as bool without explicit type).

### 3. Helm `set` Type Coercion
Helm's `set` interprets `"true"` as boolean. For Kubernetes labels (which must be strings), always use `type = "string"` in the Terraform Helm provider's `set` block.

### 4. EKS Auth Mode Migration
AWS blocks `CONFIG_MAP` → `API` directly. Must use `API_AND_CONFIG_MAP` as intermediate step.

### 5. Terraform `min()` with Lists
`min([for ...])` fails — needs spread: `min([for ...]...)`.

### 6. Helm `pending-upgrade` Recovery
When Terraform is killed mid-Helm-upgrade:
```bash
helm rollback tracecat <prev-revision> -n tracecat --no-hooks
```
If rollback fails (e.g., resource conflict), delete the pending release secret directly.

### 7. Shell `!` in AWS Secret IDs
`rds!db-...` causes zsh history expansion. Use Python subprocess or escape properly.
