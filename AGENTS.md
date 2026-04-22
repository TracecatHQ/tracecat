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
uv run pre-commit install
```

If you update dependencies, regenerate and reinstall the lockfile explicitly:

```bash
rm uv.lock && uv sync
# or
uv pip compile pyproject.toml -o uv.lock
uv sync
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

Common `just cluster` commands:

```bash
just cluster up -d
just cluster up -d --seed
just cluster ps
just cluster logs api
just cluster logs -f api
just cluster restart api
just cluster down
just cluster rm
just cluster attach api
just cluster db
just cluster ports
just cluster list
```

Use `just cluster up -d` when you need PostgreSQL, Temporal, integration tests,
or live service logs.

## Testing

```bash
just test
uv run pytest tests/unit
uv run pytest tests/integration
uv run pytest tests/registry
uv run pytest tests/unit/test_functions.py -x --last-failed
uv run pytest tests/unit -n auto
uv run pytest -k "keyword"
uv run pytest -m "not slow and not temporal"
uv run pytest -m temporal
just bench
pnpm -C frontend test
just temporal-stop-all
```

## Linting, typechecking, and pre-push verification

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

Useful aliases and focused commands:

```bash
just fix
just lint-fix
just lint-fix-app
just lint-fix-ui
cd frontend && pnpm lint
cd frontend && pnpm format:write
cd frontend && pnpm check
just typecheck
uv run basedpyright tracecat/api/
```

Recommended pre-push hook:

```bash
cat > .git/hooks/pre-push <<'EOF'
#!/bin/sh
set -e

uv run ruff check --fix .
pnpm -C frontend exec biome check --write .

git diff --exit-code

uv run ruff check .
uv run ruff format --check .
uv run basedpyright --warnings --threads 4
pnpm -C frontend check
pnpm -C frontend run typecheck
EOF

chmod +x .git/hooks/pre-push
```

Pre-commit hooks cover Ruff, Gitleaks, YAML/TOML validation, UV lock sync,
frontend client generation when relevant, Python type checks, frontend Biome
checks, and frontend type checks.

## Code generation

```bash
just gen-client-ci
just gen-api
just gen-integrations
just gen-functions
```

## Repo-wide rules

- Pin dependencies to exact versions in `pyproject.toml`. Do not switch to
  range-based constraints.
- Do not bypass commit signing with `--no-gpg-sign` or `--no-verify`. If
  signing is broken, stop and ask the user to fix it.
- Do not assume PostgreSQL superuser access in migrations, queries, or scripts.
- Never add methods to `tracecat/db/models.py`; keep database models minimal.
- Use `pnpm` instead of `npm`, and prefer `rg` over slower text-search tools.
- Ask clarifying questions when the task lacks enough context to make a safe
  change.

## CI and workflow security

- Never add `pull_request_target` to GitHub Actions in this repo.
- Use `push`, `pull_request`, and protected branch or tag triggers instead of
  `pull_request_target`.
- Treat `workflow_dispatch` as a privileged path, not a convenience default.
- Guard privileged manual workflows with `TRUSTED_CI_ACTORS_JSON`.
- If another workflow triggers guarded `workflow_dispatch`, account for
  `github-actions[bot]` explicitly instead of weakening the allowlist.
- Keep workflow permissions read-only by default and grant write scopes only at
  the job level when a step demonstrably needs them.
- Do not add `pull-requests: write`, `packages: write`, or `id-token: write`
  unless a specific job step requires them.
- Use protected environments for secret-backed jobs when possible.
- Keep `CROSS_REPO_AUTOMATION_APP_PRIVATE_KEY` in the `release` environment and
  `CUSTOM_REPO_SSH_PRIVATE_KEY` in the `internal-registry-ci` environment.
- External fork PRs must not reach secret-backed or private-infrastructure jobs.
- Release automation should validate trusted inputs before mutating tags,
  releases, downstream repos, or registries.
- Use `concurrency` on publishing and downstream-dispatch workflows to avoid
  duplicate runs racing each other.
- If you change workflow logic, review triggers, permissions, environment use,
  and trusted-input validation before considering the change done.

## Key files

- `pyproject.toml`: Python dependencies and tool config.
- `frontend/package.json`: Frontend dependencies and scripts.
- `docker-compose.dev.yml`: Local development stack.
- `alembic.ini`: Alembic config.
- `scripts/cluster`: Cluster orchestration entrypoint.

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

## Pull request descriptions

- Never use `gh pr create --body "..."` when the body includes Markdown or
  backticks.
- Write the PR body to a file with a single-quoted heredoc (`<<'EOF'`) and pass
  it with `gh pr create --body-file <file>`.
- After creating or editing a PR body, verify it with
  `gh pr view <pr-number> --json body --jq .body`.
- If formatting is wrong, fix it with `gh pr edit <pr-number> --body-file` and
  re-verify.
- Keep auto-generated PR content from cubic unless the user explicitly asks to
  remove it.

## Services and logging

- Prefer `just cluster logs <service>` and `just cluster logs -f <service>` for
  service logs.
- Use `just cluster ps` to inspect running services and `just cluster restart`
  to bounce a service after code changes.
- Use `just cluster attach <service>` when you need a shell inside a container.
- Avoid raw `docker` and `docker compose` for normal Tracecat stack management
  unless you are intentionally working with an existing non-`just cluster`
  stack.

## Registry and templates

- Registry templates live in
  `packages/tracecat-registry/tracecat_registry/templates/`.
- Use the `tools.{integration_name}` namespace for integrations.
- Keep template expressions platform-native. For anything complex, prefer
  `core.script.run_python` over dense inline expressions.
- When adding SDK helpers, verify the exact request path and add or update a
  regression test that covers it.
