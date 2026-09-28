# Tracecat agent notes

Repo-wide guidance only. Nested `AGENTS.md` files load when you work under
their directory and carry the detailed rules:

- `tracecat/AGENTS.md`: backend Python, typing, services, SQLAlchemy, config.
- `frontend/AGENTS.md`: React, TypeScript, and UI conventions.
- `packages/tracecat-registry/AGENTS.md`: integrations and templates.
- `alembic/AGENTS.md`: migration safety and expand/contract rules.
- `deployments/AGENTS.md`: Fargate, Terraform, and deployment review.
- `docs/AGENTS.md`: documentation structure and writing rules.
- `.github/AGENTS.md`: GitHub Actions security rules.

## Setup

Use `uv` for Python commands and `pnpm` for frontend commands. Prefer `rg`
over slower text search and `fd` over `find`.

```bash
uv sync
pnpm install --dir frontend
uv run pre-commit install
```

If you change dependencies, regenerate the lockfile with
`rm uv.lock && uv sync`. Pin dependencies to exact versions in
`pyproject.toml`; never switch to range constraints.

## Development stack

Before using `just cluster`, check whether a `tracecat` stack already exists
with `docker compose ls --filter name=tracecat`. If it does, decide whether to
keep using `docker compose` against it or use `just cluster` for this worktree.

- Prefer `just cluster` (`up -d`, `up -d --seed`, `ps`, `logs -f api`,
  `restart api`, `attach api`, `db`, `ports`) over raw `docker compose` for
  Tracecat services, logs, and restarts.
- Never remove volumes with `docker compose down -v`, `docker volume rm`,
  `just cluster rm`, or similar unless the user explicitly asks for it and
  confirms data loss is acceptable.
- Bring the cluster up when work needs PostgreSQL, Temporal, integration
  tests, or live service logs.

## Testing and verification

```bash
just test
uv run pytest tests/unit -n auto
uv run pytest -m "not slow and not temporal"
uv run pytest -m temporal
pnpm -C frontend test
just temporal-stop-all
```

Run the autofixers before final verification, then the same checks CI runs:

```bash
uv run ruff check --fix . && pnpm -C frontend exec biome check --write .
uv run ruff check . && uv run ruff format --check .
uv run basedpyright --warnings --threads 4
pnpm -C frontend check && pnpm -C frontend run typecheck
```

Regenerate the frontend client with `just gen-client-ci` after backend schema
changes.

## Repo-wide rules

- Do not bypass commit signing with `--no-gpg-sign` or `--no-verify`. If
  signing is broken, stop and ask the user to fix it.
- Never copy customer-provided identifiers, customer names, URLs, tenant IDs,
  subscription IDs, workspace names, resource group names, incident IDs, emails,
  domains, tokens, or other potentially sensitive values into tests, docs,
  fixtures, snapshots, examples, logs, committed code, commit messages, PR or
  issue titles/bodies, PR comments, issue comments, review comments, or any
  other published repository text. Use generic phrasing such as "affected
  customer" or clearly synthetic placeholders instead, and search for the
  original strings before committing, pushing, or publishing PR/issue text.
  Exception: the workspace sync feature may publish the source workspace name
  and initiating user's email in the generated sync PR body because that
  attribution is product behavior for user-initiated sync PRs.
- Boolean env vars exposed through Docker Compose use `${VAR:-default}`, never
  `${VAR}`, `VAR=`, or a hardcoded literal, so `.env` overrides still work. In
  `.env.example`, use an explicit `true` or `false`, never a blank value, and
  update `tests/unit/test_config.py` when adding deployment env files.
- Keep `.env.example` focused on settings ordinary open-source and self-hosted
  users are expected to configure. Advanced tuning knobs and operator-only
  overrides stay overrideable through deployment configuration instead; being
  supported by config or Compose is not a reason to advertise a variable.
- Never add `pull_request_target` to GitHub Actions. Read `.github/AGENTS.md`
  before changing any workflow.
- Read `deployments/AGENTS.md` before changing any root `docker-compose*.yml`
  file. Infrastructure changes are reviewed across Compose, Fargate, and the
  separate `TracecatHQ/k8s` repository.
- Ask clarifying questions when the task lacks enough context to make a safe
  change.

## Pull requests

- The title is the changelog line and the squash-commit subject. Format:
  `<type>(<scope>)!: <description>`, under 72 characters. Validate with
  `just check-pr-title "<title>"` before opening the PR.
- Types and scopes are a closed vocabulary in `.github/commit-conventions.toml`.
  Never add types, scopes, aliases, or labels (`gh label create`) to make a
  title pass. If something seems genuinely missing, stop and say so; a human
  decides.
- Vendor names are not scopes: write `feat(integrations): add Jira issue
  search`. At most two scopes, joined with `+`. GitHub Actions work is a bare
  `ci:`; workflow-engine work is `engine`.
- Write PR bodies to a file with a single-quoted heredoc and pass
  `--body-file`; never `--body` with Markdown. Verify with
  `gh pr view <n> --json body --jq .body`.
- Every PR body includes a LOC breakdown table (Logic, Tests, Infra/config,
  Docs, Generated; `+` and `-` columns) computed from
  `git diff --numstat <base>...HEAD`.
- Keep auto-generated PR content from cubic unless the user asks to remove it.
- The `make-pr` skill holds the full conventions: scope disambiguation,
  deprecations, dependency bumps, labels, and revert titles. `CONTRIBUTING.md`
  has the human-facing version.
