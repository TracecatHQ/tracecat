# Contributing to Tracecat

Thank you for your interest in contributing to Tracecat!

## Before You Begin

Join our [Discord](https://discord.gg/H4XZwsYzY4) and the `#contributors` channel to get started.

## What We Accept PRs For

We currently accept pull request contributions for:

* Integration updates, fixes, or additions
* API updates, fixes, or additions
* Updates to the Tracecat MCP server
* UI fixes
* Small, concrete UI improvements or suggestions

For larger or more product-opinionated changes, please open a discussion with us in Discord before starting work.

This includes, but is not limited to, changes that touch:

* Core platform
* Secrets management
* Workflow runtime or execution behavior
* Authentication, authorization, or permissions
* Security-sensitive infrastructure
* Major product behavior or architecture

We want to avoid wasted work and make sure larger changes fit the direction of the project before you invest time in implementation.

## Security-Sensitive Files

We do **not** accept pull requests that modify anything under `.github`.

Changes to GitHub Actions, workflows, repository automation, or CI/CD configuration can introduce supply chain risk. For that reason, PRs touching `.github` will be automatically closed.

## Issues Before Pull Requests

Please create a GitHub issue before opening a pull request.

You may open a PR alongside an issue for small, clearly scoped fixes, such as integration fixes, API updates, or UI bugs. Use your judgment: if the change is large, touches core product behavior, or is likely to be opinionated, open the issue or Discord discussion first and wait for feedback before implementing.

## Feature Requests

Please open a [GitHub issue](https://github.com/TracecatHQ/tracecat/issues/new/choose) with the `Feature request` template.

You can also join our Discord and discuss the feature request with the community before opening an issue.

## Bug Reports

If you discover a bug, either:

* Open a [GitHub issue](https://github.com/TracecatHQ/tracecat/issues/new/choose) with the `Bug report` template
* Post a question in our Discord `#questions` channel

We only accept bug reports that meet the following criteria:

* Has a clear, descriptive title
* Includes a clear reproducible example
* Clearly states the Tracecat version
* Clearly states the environment where the bug was encountered, such as local, VM, AWS, Kubernetes, or another setup
* Explains when the bug started occurring
* Notes whether the behavior was working previously

## Development Setup

> [!NOTE]
> Check out our [development setup guide](/docs/development-setup) in the docs for more information.

We use `docker compose` and the `docker-compose.dev.yml` files for development.

To set up your development environment, run:

```bash
just cluster up -d --seed
```

This starts the development environment and seeds a test user:

```txt
test@tracecat.com / password1234
```

You can then access the application at http://localhost:80.

> [!IMPORTANT]
> `--seed` creates a test user only. Superadmin is determined by `TRACECAT__AUTH_SUPERADMIN_EMAIL` in `.env` set via `./env.sh`, and the first signup or login with that email becomes the organization owner.

## PR and Commit Message Guidelines

We follow the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) specification for pull request titles. This repository squash-merges, so the pull request title becomes the commit subject and the line users read in the release notes.

```txt
<type>(<scope>)!: <description>
```

One line describes the whole pipeline: the type picks a type label, the scope picks an area label, and the first release-notes category that matches one of those labels decides which section the change appears under.

These rules are enforced from 2026-09-01. From that date every pull request is checked automatically, including one opened earlier, whenever you push to it or edit it. A failing check is a title edit away from green — no rebase, no force push. Anything merged before that date was never checked, so do not copy an older commit subject as an example: much of the history uses spellings the checker now rejects.

Check a title before you open the pull request:

```bash
just check-pr-title "feat(cases): add case duplication"
```

### Types

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

Nothing else is accepted. Formatting is not a type: ruff and biome run on commit, so whitespace never ships as its own pull request.

### Scopes

Add a scope whenever the change belongs to one product area. Leave it off only when the change is genuinely repo-wide.

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
| `workflows` | GitHub Actions when the type is ci, the workflow engine otherwise |

<!-- END commit-conventions:scopes -->

Five distinctions cover most of the doubt:

- `actions` is the built-in `core.*` actions a user calls in a workflow. `functions` is the `FN.*` inline expression functions. `engine` is the Temporal workers and executors that run them. The first two are catalogs of things the platform offers; the third is the machinery.
- `cases` and `tables` are core platform features with their own scopes and their own sections. Neither folds into `api` or `engine`.
- `engine` is what runs; `infra` is what it runs on. If the change could ship by redeploying the same image, it is `infra`.
- `audit` is the security audit log: what a workspace records about who did what, for someone reviewing it later. `logging` is application telemetry, what an operator reads to debug Tracecat itself. Audit work renders under Security, telemetry under Observability.
- Vendor names are not scopes. Write `feat(integrations): add Jira issue search` and name the vendor in the description, where it is readable and searchable.

At most two scopes, joined with `+`, as in `feat(cases+actions): add a case linking action`. A change lands in the highest-ranked section its labels match, so that example appears under Case management, not Core actions: the reader cares that case management gained something, not which package it was built from. Needing three scopes usually means the pull request should be split.

### Breaking changes and deprecations

Put `!` before the colon to mark a breaking change: `feat(api)!: drop the v1 webhook payload`.

Removing something takes three steps, usually across three releases:

1. Announce it: `deprecation(integrations): tools.x.list_signals in favour of tools.x.search_alerts`. The description has to name the replacement, or say `with no replacement`.
2. Warn in the code in the same pull request, with the `deprecated="Use ... instead"` argument on the registry action.
3. Remove it: `feat(integrations)!: remove tools.x.list_signals`.

### Dependencies

Routine bumps and Low, Moderate, or High severity advisories are `build(deps):`. Reserve `security(deps):` for Critical unauthenticated remote-code-execution advisories, so the Security section stays worth dropping everything for.

### Where the rules live

`.github/commit-conventions.toml` holds the vocabulary, and `.github/release-drafter.yml` maps labels to sections. Pull requests that touch `.github/` are closed automatically, so open an issue if a scope you need is missing.

## Release Process

Tracecat loosely follows the [Semantic Versioning](https://semver.org/) specification for releases.

We are currently on the `1.0.0-beta.xyz` version series.

## License

This project is licensed under the open source, copyleft [GNU Affero General Public License v3.0](LICENSE).

By contributing to this project, you agree that your contributions will be licensed under the same license.

Thank you for taking the time to contribute to Tracecat!
