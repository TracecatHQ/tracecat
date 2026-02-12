# Fargate 1.0.0-beta upgrade TODOs

Source of truth: `/Users/chris/repos/tracecat/docker-compose.yml`

## 1. Service topology and entrypoints
- [x] Add ECS service for `agent-executor` with command `python -m tracecat.agent.worker`.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/ecs-agent-executor.tf` (new), `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/main.tf`
- [x] Convert legacy `executor` handling to a 1.0+ only worker model (drop legacy HTTP executor mode and related toggles).
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/ecs-executor.tf`, `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/variables.tf`, `/Users/chris/repos/tracecat/deployments/fargate/variables.tf`, `/Users/chris/repos/tracecat/deployments/fargate/main.tf`
- [x] Align service dependencies (`depends_on`) with compose startup requirements for API/worker/executor/agent-executor.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/ecs-api.tf`, `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/ecs-worker.tf`, `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/ecs-executor.tf`, `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/ecs-agent-executor.tf`

## 2. Environment variable parity (compose -> ECS)
- [x] Update `api` env vars to include 1.0 beta settings and remove obsolete/no-op values.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/locals.tf`
- [x] Update `worker` env vars to include 1.0 beta settings and remove obsolete/no-op values.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/locals.tf`
- [x] Update `executor` env vars to include 1.0 beta settings and remove obsolete/no-op values.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/locals.tf`
- [x] Add `agent-executor` env vars matching compose expectations.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/locals.tf`
- [x] Add/adjust optional secret mappings required by updated env model.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/secrets.tf`, `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/variables.tf`, `/Users/chris/repos/tracecat/deployments/fargate/variables.tf`, `/Users/chris/repos/tracecat/deployments/fargate/main.tf`

## 3. S3/blob storage updates
- [x] Add new workflow artifact bucket required for result externalization/collection manifests.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/s3.tf`
- [x] Update IAM policies so runtime roles can access new/updated blob buckets.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/iam.tf`
- [x] Wire new bucket env vars into API/worker/executor/agent-executor.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/locals.tf`
- [x] Add outputs for any new buckets.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/outputs.tf`, `/Users/chris/repos/tracecat/deployments/fargate/outputs.tf`

## 4. ECS networking/security/service-connect
- [x] Add service-connect discovery and SG compatibility for new `agent-executor` service.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/ecs-agent-executor.tf`, `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/security_groups.tf`

## 5. Terraform interface cleanup for clean 1.0 break
- [x] Update default image tag to a `1.0.0-beta.x` default.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/variables.tf`, `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/variables.tf`
- [x] Remove pre-1.0 compatibility variables that are no longer needed.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/variables.tf`, `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/variables.tf`, `/Users/chris/repos/tracecat/deployments/fargate/main.tf`
- [x] Introduce any new sizing/count variables needed for `agent-executor`.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/variables.tf`, `/Users/chris/repos/tracecat/deployments/fargate/modules/ecs/variables.tf`, `/Users/chris/repos/tracecat/deployments/fargate/main.tf`

## 6. Documentation
- [x] Add dedicated README for this stack with architecture, required secrets, deploy flow, and key variables.
  - Files: `/Users/chris/repos/tracecat/deployments/fargate/README.md` (new)
- [x] Update inaccuracies in AWS ECS docs to match this repository and the upgraded stack.
  - Files: `/Users/chris/repos/tracecat/docs/self-hosting/deployment-options/aws-ecs.mdx`

## 7. Validation
- [x] Run formatting/validation for Terraform files changed in this update.
  - Commands: `terraform -chdir=/Users/chris/repos/tracecat/deployments/fargate fmt -recursive`, `terraform -chdir=/Users/chris/repos/tracecat/deployments/fargate validate` (if provider init is available)
