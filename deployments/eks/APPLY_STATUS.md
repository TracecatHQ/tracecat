# Terraform Apply Status - 2026-02-24

## Background
Fixing drift between Terraform state and deployed EKS cluster in us-west-2.
Original plan: **17 to add, 9 to change, 6 to destroy**.

## Current Status
- **Terraform apply running** — Helm upgrade in progress (~20s in), deploying `1.0.0-beta.17`
- **All app pods healthy** — API (2), Worker (4), Executor (4), Agent Executor (2), UI (2), Temporal (all)
- **Postgres secret stable** — Helm annotations stripped, `deletionPolicy: Retain` in place

## What's Been Completed

### Infrastructure (all applied successfully)
- **ElastiCache**: Scaled from 1x cache.t3.micro → 3x cache.t4g.medium, Multi-AZ + auto-failover enabled
- **EKS Node Group**: Replaced t4g.2xlarge → m7g.2xlarge, scaled from 5 → 10 nodes on-demand
- **Spot Node Group**: Created (2x spot nodes, m7g/m6g/c7g.2xlarge)
- **EKS Cluster**: Auth mode changed CONFIG_MAP → API_AND_CONFIG_MAP (intermediate step)
- **Security Groups**: Removed VPC CIDR fallback rules on RDS + ElastiCache SGs
- **DNS SG Rules**: 4 new cluster DNS rules for pod SGs (TCP+UDP for postgres+redis)
- **WAF**: Created WAF Web ACL + regex pattern set for attachments endpoint
- **ExternalDNS**: IAM role + policy + Helm chart deployed (replaces null_resource.dns_record)
- **IAM**: S3 role policy updated with SecretsManager access for RDS credentials
- **OIDC Provider**: Thumbprint updated
- **RDS Password Rotation**: Created (365 day schedule)
- **null_resources**: 5 destroyed (replaced by kubernetes_manifest and kubernetes_job resources)
- **ClusterSecretStore**: Imported + updated
- **Postgres ExternalSecret**: Created via Terraform with `deletionPolicy: Retain`
- **Temporal DB Job**: kubernetes_job_v1.create_temporal_databases created
- **RDS**: Updated (apply_immediately — modifications accepted and applied)
- **Image tag**: Updated to `1.0.0-beta.17`

### Code Fixes Applied
1. **`modules/eks/cluster.tf:37`**: `authentication_mode = "API_AND_CONFIG_MAP"` (intermediate; AWS blocks CONFIG_MAP → API directly)
2. **`modules/eks/main.tf:107-123`**: Fixed `min([...])` → `min([...]...)` — spread operator for variadic `min()`
3. **`modules/eks/external-secrets.tf:69`**: Added `field_manager { force_conflicts = true }` to postgres ExternalSecret
4. **`modules/eks/external-secrets.tf:91`**: Added `deletionPolicy = "Retain"` — Secret survives ExternalSecret deletion
5. **`modules/eks/helm.tf:82-83`**: `atomic = false`, `cleanup_on_fail = false` — prevents rollback doom loop
6. **`modules/eks/helm.tf:435-438`**: Added `temporal.schema.podLabels.tracecat.com/access-postgres = "true"` with `type = "string"` — schema job gets RDS security group
7. **`modules/eks/helm.tf:429-432`**: Added `type = "string"` to existing server podLabels — consistency fix
8. **EKS Access Entry**: Created for `chris-dev` IAM user with AmazonEKSClusterAdminPolicy

### Manual Cluster Fixes
- Stripped Helm ownership annotations from postgres ExternalSecret (server-side apply with `--field-manager=terraform`)
- Created `tracecat-postgres-credentials` Secret directly via kubectl to break doom loop
- Multiple Helm rollbacks via `helm rollback --no-hooks` to clear `pending-upgrade` states
- Deleted stale Helm release secrets to recover from killed applies

## What Remains After Helm Completes

### If Helm succeeds:
1. **Flip auth mode to `API`**: Change `modules/eks/cluster.tf:37` from `"API_AND_CONFIG_MAP"` to `"API"`:
   ```bash
   env -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN terraform apply -auto-approve
   ```
2. **Verify zero drift**: `terraform plan -detailed-exitcode` should exit 0
3. **Clean up old schema jobs**: `kubectl delete job -n tracecat tracecat-temporal-schema-18 tracecat-temporal-schema-31`

### If Helm fails:
- Check `helm list -n tracecat -a`
- If `pending-upgrade`: `helm rollback tracecat <prev> -n tracecat --no-hooks`
- Then re-run terraform apply

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

**Fixes**:
- `deletionPolicy: Retain` on ExternalSecret
- `atomic = false` on Helm release
- Strip Helm annotations (`meta.helm.sh/release-name`, `meta.helm.sh/release-namespace`) from ExternalSecret
- Create Secret directly via kubectl as immediate unblock

**Prevention**: When migrating a resource from Helm to Terraform:
1. Always set `deletionPolicy: Retain` on ExternalSecrets
2. Strip Helm annotations BEFORE upgrade
3. Use `atomic = false` during migration
4. Ensure Secret exists BEFORE Helm upgrade starts

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
