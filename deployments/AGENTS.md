# Deployment notes

Rules for work under `deployments/` and the root `docker-compose*.yml` files.

## Review across targets

- Infrastructure changes must be reviewed across every relevant target:
  `docker-compose*.yml` and `deployments/fargate/`. Kubernetes infrastructure
  lives in the separate `TracecatHQ/k8s` repository and must be reviewed there
  when relevant.
- Check the matching `values.yaml`, `variables.tf`, and `main.tf` files before
  closing out infra work.
- A change that could ship by redeploying the same image is `infra` scope, not
  `engine`.

## Fargate and ECS Service Connect

- ECS Service Connect clients should explicitly depend on the ECS services that
  publish the Service Connect aliases they resolve. This avoids startup and
  rollout races where a client task starts before the provider alias is
  registered or stable. Follow the existing UI-to-API ordering pattern; for
  example, an agent-executor service that calls the managed LiteLLM alias should
  depend on the LiteLLM ECS service unless that would create a dependency cycle.
- When a cycle appears, prefer breaking the unnecessary provider dependency
  rather than leaving the Service Connect client unordered.
