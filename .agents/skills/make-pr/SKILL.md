---
name: make-pr
description: Create, retitle, or label a pull request for the current branch. Holds the full commit-convention rules (types, scopes, deprecations, dependency bumps, labels, reverts) and the PR body requirements. Use whenever opening a PR, fixing a rejected PR title, or choosing a scope or label.
---

# Make a pull request

Create a PR for the current branch.

## Requirements

- Do not stage unstaged changes unless instructed otherwise.
- If the repo is checked out on `main`, first create a new branch from main and
  open the PR from it. Never make changes on main.
- Keep the description concise. Include the LOC breakdown table below.
- Validate the title with `just check-pr-title "<title>"` before opening. It
  reports every violation at once, each with a stable code such as
  `unknown-scope`.
- Additional instructions, if provided, override anything above: $ARGUMENTS

## Titles

- The PR title is the changelog line. Release Drafter renders it verbatim into
  the release notes, prefix included, so write it as a user-facing change
  description. The repo squash-merges, so the title also becomes the commit
  subject, plus ` (#NNNN)`.
- The format is `<type>(<scope>)!: <description>`. Type picks a type label,
  scope picks an area label, and the first matching category in
  `.github/release-drafter.yml` picks the release-notes section.
- Keep the first line under 72 characters. Over-length warns, it does not fail.
- Mark breaking changes with `!` before the colon, e.g. `feat(api)!: ...`. The
  `!` routes the PR to the Breaking changes section. There is no `breaking:`
  type.
- Malformed titles like `feat(cases) ENG-1597: ...` (no colon after the scope)
  get no automatic label and render without a heading in the draft release.
- GitHub's revert button generates `Revert "fix(agents): ..."`, and the checker
  rejects it: no type, so no section. Retitle to
  `revert(<scope>): <description>`, keeping the scope of the change being
  reverted. The `revert-wrapper` error spells the replacement out.
- Do not copy a pre-2026-09-01 commit subject as an example of house style.
  Most of `git log` predates these rules and was never checked.

Allowed types:

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

## Scopes

Add a scope for anything touching a product area. Leave it off only when the
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
| `enterprise` | Enterprise edition and tiers; pair it with the area it changes |
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
- `enterprise` says who may use a change, not what the change is, so it never
  decides the section. Pair it with the area: `feat(enterprise+cases)` lands in
  Case management, and a bare `feat(enterprise)` falls to Features.
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
  linking action`. A change lands in the highest-ranked section its labels
  match, so that example appears under Case management, not Core actions.
  Needing three scopes usually means the PR should be split.
- Scopes that name two different things are rejected outright: `app`, `dev`,
  `config`, `service`, `tracecat`, `ai`, `workflows`. `app` historically meant
  the backend, not the frontend.
- `workflows` reads as GitHub Actions to one person and as the workflow engine
  to another. GitHub Actions work is a bare `ci:` with no scope, and
  workflow-engine work is `engine`.
- Everything else the checker rejects is an old spelling with a canonical
  replacement it will name for you, e.g. `registry` to `integrations`, `agent`
  to `agents`, `ee` to `enterprise`, `udfs` and `core` to `actions`.

## Never invent vocabulary

- The vocabulary is closed. Use only the types, scopes and labels that already
  exist in `.github/commit-conventions.toml` and `gh label list`.
- Do not add entries to `[types]`, `[scopes]`, `[scope_aliases]` or
  `[legacy_scopes]`, and do not run `gh label create`, even when a change seems
  not to fit. It usually does fit: a vendor name belongs in `integrations` with
  the vendor named in the description, and a change that needs a third scope is
  a PR that should be split.
- A failing check is the system working, not a reason to widen the vocabulary.
  `feat(jira): ...` is meant to fail; the fix is
  `feat(integrations): add Jira issue search`, not a new `jira` scope.
- If you believe a label, type or scope is genuinely missing, stop and say so.
  Name what you think is missing and why, then leave it to a human. Scopes and
  labels decide release-note section headings, which are user-facing, so new
  vocabulary needs engineering and GTM review before anyone adds it.

## Deprecations

Removing something takes three PRs, usually across three releases:

1. Announce: `deprecation(<scope>): <thing> in favour of <replacement>`. The
   description must name a replacement or say `with no replacement`; the
   checker fails otherwise.
2. Warn in the code in the same PR, via `deprecated="Use ... instead"` on the
   registry action.
3. Remove: `feat(<scope>)!: remove <thing>`, which lands under Breaking
   changes.

## Dependencies

- Routine Dependabot patches and Low, Moderate, or High severity advisories
  are `build(deps):` and land under Dependencies.
- Reserve `security(deps):` for Critical unauthenticated remote-code-execution
  advisories. Security stays a drop-everything section only if it is rare.

## Labels

- The autolabeler assigns type and area labels from the title once the PR is
  opened, so do not hand-label in the normal case. Hand-label only fork PRs
  (the autolabeler cannot write to them) and to add nuance the title cannot
  express.
- Before hand-labeling, list existing repo labels with `gh label list` and
  pick from that set.
- The autolabeler only ever adds. Retitle a pull request and the labels its
  old title earned stay put, so `fix(workflows): ...` retitled to `ci: ...`
  keeps `engine` alongside the new `cicd` and lands in two sections. Remove the
  stale ones yourself:
  `gh api --method DELETE repos/TracecatHQ/tracecat/issues/<pr>/labels/<label>`.
- `gh pr edit` subcommands fail on this repo because of the Projects-classic
  deprecation. Apply labels with
  `gh api repos/TracecatHQ/tracecat/issues/<pr-number>/labels -f "labels[]=<label>"`.

## Descriptions

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
  removed lines by kind of change:
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

## Changing the conventions

For humans making an approved change: `.github/commit-conventions.toml` is the
source of truth. Edit it, then re-verify with `just check-pr-title` and
`uv run pytest tests/unit/test_commit_conventions.py`, which fails if the
autolabeler regexes, the category labels, or the tables above drift from it.
Re-derive any quoted counts with `just audit-conventions prefixes`.
