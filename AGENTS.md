# Tracecat agent notes

Use this file for repo-wide guidance. Prefer the more specific notes in nested
`AGENTS.md` files when you are working inside those paths.

## Path-specific notes

- `frontend/AGENTS.md`: Frontend, React, TypeScript, and UI conventions.
- `tracecat/AGENTS.md`: Backend Python, service, typing, SQLAlchemy, and API
  conventions.
- `docs/AGENTS.md`: Documentation structure and writing rules.

## Repo map

- `tracecat/`: API, services, workflow engine, executor, auth, and shared
  backend code.
- `frontend/`: Next.js app, React UI, generated client, and frontend tests.
- `packages/tracecat-registry/`: Integrations, templates, and registry SDK.
- `packages/tracecat-admin/`: Operator CLI.
- `packages/tracecat-ee/`: Enterprise features and shims.
- `alembic/`: Database migrations.
- `deployments/`: Docker, Fargate, EKS, and Helm deployment targets.

## Setup and verification

Use `uv` for Python commands and `pnpm` for frontend commands.

```bash
uv sync
pnpm install --dir frontend
```

Run autofixers before final verification when you change Python or frontend
code:

```bash
uv run ruff check --fix .
pnpm -C frontend exec biome check --write .
```

Core verification:

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright --warnings --threads 4
pnpm -C frontend check
pnpm -C frontend run typecheck
```

## Development stack safety

Before using `just cluster`, check whether a `docker compose` stack named
`tracecat` is already running:

```bash
docker compose ls --filter name=tracecat
```

- If a stack already exists, decide whether to keep using `docker compose`
  against that stack or use `just cluster` for this worktree.
- Never remove volumes with `docker compose down -v`, `docker volume rm`,
  `just cluster rm`, or similar commands unless the user explicitly asks for it
  and confirms data loss is acceptable.
- Prefer `just cluster` for live Tracecat services, logs, restarts, and local
  database-backed development.

## Repo-wide rules

- Pin dependencies to exact versions in `pyproject.toml`. Do not switch to
  range-based constraints.
- Do not bypass commit signing with `--no-gpg-sign` or `--no-verify`. If
  signing is broken, stop and ask the user to fix it.
- Do not assume PostgreSQL superuser access in migrations, queries, or scripts.
- Never add methods to `tracecat/db/models.py`; keep database models minimal.
- Use `pnpm` instead of `npm`, and prefer `rg` over slower text-search tools.

## CI and workflow security

- Never add `pull_request_target` to GitHub Actions in this repo.
- Keep workflow permissions read-only by default and grant write scopes only at
  the job level when a step demonstrably needs them.
- Use protected environments for secret-backed jobs when possible.
- External fork PRs must not reach secret-backed or private-infrastructure jobs.
- If you change workflow logic, review triggers, permissions, environment use,
  and trusted-input validation before considering the change done.

## Infra and migrations

- Infrastructure changes must be reviewed across all relevant deployment
  targets: `docker-compose*.yml`, `deployments/fargate/`,
  `deployments/k8s/eks/`, `deployments/k8s/eks/modules/eks/`, and
  `deployments/k8s/helm/`.
- Check the matching `values.yaml`, `variables.tf`, and `main.tf` files before
  closing out infra work.
- For Alembic work, bring up the database first, check the cluster port with
  `just cluster ports`, and prefer `uv run alembic revision --autogenerate`
  before manually editing a new migration.

## Registry and templates

- Registry templates live in
  `packages/tracecat-registry/tracecat_registry/templates/`.
- Use the `tools.{integration_name}` namespace for integrations.
- Keep template expressions platform-native. For anything complex, prefer
  `core.script.run_python` over dense inline expressions.
- When adding SDK helpers, verify the exact request path and add or update a
  regression test that covers it.
