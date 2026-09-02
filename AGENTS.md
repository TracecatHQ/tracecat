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

## Fargate Deployment Notes

- ECS Service Connect clients should explicitly depend on the ECS services that
  publish the Service Connect aliases they resolve. This avoids startup and
  rollout races where a client task starts before the provider alias is
  registered or stable. Follow the existing UI-to-API ordering pattern; for
  example, an agent-executor service that calls the managed LiteLLM alias should
  depend on the LiteLLM ECS service unless that would create a dependency cycle.
- When a cycle appears, prefer breaking the unnecessary provider dependency
  rather than leaving the Service Connect client unordered.

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
- Do not assume PostgreSQL superuser access in migrations, queries, or scripts.
- Never add methods to `tracecat/db/models.py`; keep database models minimal.
- Never use untyped dictionaries unless there is a compelling reason. Model
  structured data with a dataclass or Pydantic model as appropriate; if
  dictionary semantics are required, prefer `TypedDict`. Any unavoidable
  untyped-dictionary exception must include a clear nearby explanation of why
  the typed alternatives are unsuitable.
- Prefer `@dataclass(frozen=True, slots=True)` over `NamedTuple` for immutable
  structured values. It is smaller and blocks positional/iteration access, so
  fields stay named. Measured on this repo's CPython 3.12.8 (shallow instance
  size): `NamedTuple` 56 bytes, `@dataclass(frozen=True, slots=True)` 48 bytes,
  plain dataclass 344 bytes including `__dict__`. Use `NamedTuple` only when
  tuple unpacking or tuple compatibility is actually required.
- Never branch on exception or error-message strings to choose behavior, status
  codes, or retry policy. Use explicit exception types, machine-readable error
  codes in exception details, or structured error objects instead.
- Boolean environment variables in `tracecat/config.py` must use `env_bool(...)`.
  Do not add inline `.lower() == "true"`, `.lower() in (...)`, or
  `bool(os.environ.get(...))` parsing. If a boolean env var is exposed through
  Docker Compose, use `${VAR:-default}` instead of `${VAR}`, `VAR=`, or a
  hardcoded literal so `.env` overrides still work. In `.env.example`, use an
  explicit `true` or `false`, never a blank value. Update
  `tests/unit/test_config.py` when adding deployment env files.
- Keep `.env.example` focused on settings that ordinary open-source and
  self-hosted users are reasonably expected to configure. Do not list advanced
  tuning knobs or operator-only overrides when safe defaults work for nearly
  all users; keep those settings overrideable through explicit deployment
  configuration instead. A variable being supported by config or Compose is
  not, by itself, a reason to advertise it in `.env.example`.
- Use `pnpm` instead of `npm`, prefer `rg` over slower text-search tools, and
  prefer `fd` over `find` when `fd` is available.
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
  targets: `docker-compose*.yml` and `deployments/fargate/`. Kubernetes
  infrastructure lives in the separate `TracecatHQ/k8s` repository and must be
  reviewed there when relevant.
- Check the matching `values.yaml`, `variables.tf`, and `main.tf` files before
  closing out infra work.
- For Alembic work, bring up the database first, check the cluster port with
  `just cluster ports`, and prefer `uv run alembic revision --autogenerate`
  before manually editing a new migration.

## Pull requests

### Enforcement cutoff

Commit conventions are enforced from 2026-09-01. From that date the
`Commit conventions / PR title` check runs on every pull request, including one
opened earlier, on any of open, retitle, push, reopen or ready-for-review. The
autolabeler applies its labels on the same events.

There is no grandfather clause and no skip label. None is needed: every open
pull request was retitled to comply before the cutoff, so the check starts
green across the board.

Titles merged before that date were never checked and do not follow this
vocabulary. 425 of 2548 merged pull requests carry no label at all, and one
concept is spelled three ways: `integrations` 201, `registry` 103,
`integration` 76. That history is the reason `[scope_aliases]`,
`[legacy_types]` and `[legacy_scopes]` exist — the autolabeler reads every old
spelling so already-merged work still categorizes, while the checker accepts
only the canonical one.

Two consequences for agents:

- Do not copy a pre-cutoff commit subject as an example of house style. Most of
  `git log` predates these rules.
- A rejected title is never a reason to widen the vocabulary to match history.
  See "Never invent vocabulary" below.

`git log` before the cutoff is immutable and stays non-compliant. Only the
rendered release notes are normalized, by the `replacers:` block in
`.github/release-drafter.yml`.

### Titles

- The PR title is the changelog line. Release Drafter renders it verbatim into
  the release notes, prefix included, so write it as a user-facing change
  description. The repo squash-merges, so the title also becomes the commit
  subject, plus ` (#NNNN)`.
- The format is `<type>(<scope>)!: <description>`. One line describes the
  pipeline: type picks a type label, scope picks an area label, and the first
  matching category in `.github/release-drafter.yml` picks the release-notes
  section.
- Validate before opening the PR:
  `just check-pr-title "feat(cases): add case duplication"`. It reports every
  violation at once, each with a stable code such as `unknown-scope`.
- Allowed types:

  <!-- BEGIN commit-conventions:types -->

  | Type | Use it for |
  | --- | --- |
  | `build` | Packaging, wheels, images, and release tooling |
  | `chore` | Housekeeping with no user-visible effect |
  | `ci` | GitHub Actions and CI configuration |
  | `deprecation` | Announcing a deprecation |
  | `docs` | Documentation only |
  | `feat` | New user-visible behaviour |
  | `fix` | A bug fix |
  | `infra` | Deployment targets, Terraform, Helm, Compose |
  | `perf` | Measurable performance work |
  | `refactor` | Restructuring that preserves behaviour |
  | `release` | Version bumps, excluded from the release notes |
  | `revert` | Undoing a previous change |
  | `security` | Security fixes and hardening |
  | `test` | Tests only |

  <!-- END commit-conventions:types -->

- Keep the first line under 72 characters. Over-length warns, it does not fail.
- Mark breaking changes with `!` before the colon, e.g. `feat(api)!: ...`. The
  `!` routes the PR to the Breaking changes section. There is no `breaking:`
  type.
- Malformed titles like `feat(cases) ENG-1597: ...` (no colon after the scope)
  get no automatic label and render without a heading in the draft release.

### Scopes

- Add a scope for anything touching a product area. Leave it off only when the
  change is genuinely repo-wide.

  <!-- BEGIN commit-conventions:scopes -->

  | Scope | Use it for |
  | --- | --- |
  | `actions` | The built-in core.* actions a user calls in a workflow |
  | `agents` | Agent runtime, chat, presets, tools, and artifacts |
  | `api` | Backend API, auth, and organization or workspace administration |
  | `audit` | Security audit logs: who did what in a workspace |
  | `build` | Packaging and the operator CLI |
  | `cases` | Case management |
  | `deps` | Dependency bumps |
  | `docs` | Documentation and playbooks |
  | `engine` | The Temporal workers, executors, and scheduler that run workflows |
  | `enterprise` | Enterprise edition and tiers |
  | `functions` | The FN.* inline expression functions |
  | `infra` | Databases, deployments, and cloud infrastructure |
  | `integrations` | Third-party vendor connectors and registry templates |
  | `logging` | Application logging and telemetry: how Tracecat runs |
  | `mcp` | Tracecat's own MCP server |
  | `rbac` | Roles and permissions |
  | `skills` | Agent skills |
  | `tables` | Workspace tables |
  | `ui` | The Next.js app and React UI |

  <!-- END commit-conventions:scopes -->

- `actions` is the built-in `core.*` actions a user calls in a workflow;
  `functions` is the `FN.*` inline expression functions; `engine` is the
  Temporal workers and executors that run them. The first two are catalogs of
  what the platform offers, and each has its own release-notes section; the
  third is the machinery.
- `cases` and `tables` are core platform features with their own scopes and
  their own sections. Neither folds into `api` or `engine`.
- `engine` is what runs; `infra` is what it runs on. If the change could ship
  by redeploying the same image, it is `infra`.
- `audit` is the security audit log: what a workspace records about who did
  what, for someone reviewing it later. `logging` is application telemetry, what
  an operator reads to debug Tracecat itself. Audit work renders under Security,
  telemetry under Observability.
- Vendor names are not scopes. Write `feat(integrations): add Jira issue
  search` and name the vendor in the description. The autolabeler still
  absorbs vendor scopes into `integrations` so merged PRs categorize, but the
  checker rejects them.
- At most two scopes, joined with `+`, e.g. `feat(cases+actions): add a case
  linking action`. A change lands in the first section its labels match, so
  that example appears under Case management, not Core actions. Needing three
  scopes usually means the PR should be split.
- Scopes that name two different things are rejected outright: `app`, `dev`,
  `config`, `service`, `tracecat`, `ai`, `workflows`. `app` is the reason the
  list exists; it historically meant the backend, not the frontend.
- `workflows` is the one that is ambiguous by construction rather than by
  history: it reads as GitHub Actions to one person and as the workflow engine
  to another. GitHub Actions work is a bare `ci:` with no scope, and
  workflow-engine work is `engine`.
- Everything else the checker rejects is an old spelling with a canonical
  replacement it will name for you, e.g. `registry` to `integrations`, `agent`
  to `agents`, `ee` to `enterprise`, `udfs` and `core` to `actions`.

### Deprecations

Removing something takes three PRs, usually across three releases:

1. Announce: `deprecation(<scope>): <thing> in favour of <replacement>`. The
   description must name a replacement or say `with no replacement`; the
   checker fails otherwise.
2. Warn in the code in the same PR, via `deprecated="Use ... instead"` on the
   registry action.
3. Remove: `feat(<scope>)!: remove <thing>`, which lands under Breaking
   changes.

### Dependencies

- Routine Dependabot patches and Low, Moderate, or High severity advisories
  are `build(deps):` and land under Dependencies.
- Reserve `security(deps):` for Critical unauthenticated remote-code-execution
  advisories. Security stays a drop-everything section only if it is rare.

### Labels

- The autolabeler assigns type and area labels from the title once the PR is
  opened, so do not hand-label in the normal case. Hand-label only fork PRs
  (the autolabeler cannot write to them) and to add nuance the title cannot
  express.
- Before hand-labeling, list existing repo labels with `gh label list` and
  pick from that set. See "Never invent vocabulary" below.
- `gh pr edit` subcommands fail on this repo because of the Projects-classic
  deprecation. Apply labels with
  `gh api repos/TracecatHQ/tracecat/issues/<pr-number>/labels -f "labels[]=<label>"`.

### Never invent vocabulary

- The vocabulary is closed. Use only the types, scopes and labels that already
  exist in `.github/commit-conventions.toml` and `gh label list`. This applies
  equally to labels, conventional-commit types, and scopes.
- Do not add entries to `[types]`, `[scopes]`, `[scope_aliases]` or
  `[legacy_scopes]`, and do not run `gh label create`, even when a change seems
  not to fit. It usually does fit: a vendor name belongs in `integrations` with
  the vendor named in the description, and a change that needs a third scope is
  a PR that should be split.
- A failing check is the system working, not a reason to widen the vocabulary.
  `feat(jira): ...` is meant to fail; the fix is
  `feat(integrations): add Jira issue search`, not a new `jira` scope.
- If you believe a label, type or scope is genuinely missing, stop and say so.
  Name what you think is missing and why, then leave it to a human. New
  vocabulary needs discussion and review from the engineering and GTM teams
  before anyone adds it through the GitHub UI, because scopes and labels decide
  release-note section headings, which are user-facing. An agent that invents a
  scope mid-task ships a heading nobody agreed to.
- `audit` was added exactly that way on 2026-09-01. An agent hit the
  `unknown-scope` rejection telling it to write `api` on a real pull request,
  stopped rather than retitling, and named the gap; a human approved the scope.
  Nine merged pull requests had been filed under `api` because of that alias.
  The escalation is the path, and the vocabulary is still closed.

### Changing the conventions

- This section is for humans making an approved change. Agents should read the
  rule above first.
- `.github/commit-conventions.toml` is the source of truth. Edit it, then
  regenerate and re-verify: `just check-pr-title`, and
  `uv run pytest tests/unit/test_commit_conventions.py`, which fails if the
  autolabeler regexes, the category labels, or the tables above drift from it.
- Any counts quoted in these docs are a snapshot. Re-derive them with
  `just audit-conventions prefixes`; that command, not the prose, is the
  source of truth for how the repo actually writes titles.

### Descriptions

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
- Include a LOC breakdown in every PR body: categorize the diff's added and
  removed lines by kind of change, e.g.:
  - Logic: application/backend/frontend source code.
  - Tests: `tests/`, `frontend/**/*.test.*`, fixtures.
  - Infra/config: `docker-compose*.yml`, `deployments/`, `.github/`, `Dockerfile*`,
    env files, `justfile`, tool configs.
  - Docs: `docs/`, `*.md`.
  - Generated: lockfiles (`uv.lock`, `pnpm-lock.yaml`), generated API clients,
    migrations produced by autogenerate.
  Compute counts from `git diff --numstat <base>...HEAD` and render as a small
  Markdown table with one row per category and `+` / `-` columns. Omit empty
  categories; use judgment for files that straddle categories.

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
- Scope PRs for vendor connector work as `(integrations)`, not `(registry)`;
  see the Scopes rules under Pull requests.
- Keep template expressions platform-native. For anything complex, prefer
  `core.script.run_python` over dense inline expressions.
- When adding SDK helpers, verify the exact request path and add or update a
  regression test that covers it.
