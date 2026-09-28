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

GitHub's revert button generates `Revert "fix(agents): ..."`, which is rejected too: the wrapper carries no type, so nothing can file it into a section. Retitle it `revert(<scope>): <description>`, keeping the scope of the change you are reverting.

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

Seven distinctions cover most of the doubt:

- `actions` is the built-in `core.*` actions a user calls in a workflow. `functions` is the `FN.*` inline expression functions. `engine` is the Temporal workers and executors that run them. The first two are catalogs of things the platform offers; the third is the machinery.
- `cases` and `tables` are core platform features with their own scopes and their own sections. Neither folds into `api` or `engine`.
- `enterprise` says who may use a change, not what the change is, so it never decides the section. Pair it with the area: `feat(enterprise+cases)` lands in Case management, and a bare `feat(enterprise)` falls to Features.
- `engine` is what runs; `infra` is what it runs on. If the change could ship by redeploying the same image, it is `infra`.
- `workflows` is not a scope, because it reads as GitHub Actions to one person and as the workflow engine to another. A GitHub Actions change is a bare `ci:` with no scope; a workflow-engine change is `engine`.
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

Tracecat follows [Semantic Versioning](https://semver.org/). Version numbers are release trains, not a measure of how much changed: every regular release bumps the minor, and the release-notes sections say what is inside.

### Version shapes

| Tag | What it is | Cut from |
|-----|------------|----------|
| `1.X.0-alpha.N` | A build of `main` before the train is frozen. Features still land between alphas. Nothing is promised. | `main` |
| `1.X.0-alpha.N.M` | A hotfix of alpha `N`: cherry-picked fixes only, no new migrations, database-schema changes, backfills, or registry actions. | `release/1.X.0-alpha.N` |
| `1.X.0-rc.N` | The train is frozen and only fixes may land. Used when a change needs validation outside Tracecat Cloud before it ships. Most trains skip this stage. | `release/1.X` |
| `1.X.0` | The stable train. | `release/1.X` |
| `1.X.Y` | A hotfix on a shipped train: cherry-picked fixes only, no new migrations or registry actions. | `release/1.X` |
| `2.0.0-alpha.N`, `2.0.0` | A major. Same shape as a minor. | as above |

Tags are bare versions with no leading `v`. Ordering follows semver:

```
1.1.0-alpha.1 < 1.1.0-alpha.1.1 < 1.1.0-alpha.1.2 < 1.1.0-alpha.2 < 1.1.0-rc.1 < 1.1.0 < 1.1.1 < 1.2.0-alpha.1
```

### Rules

- **Prerelease suffixes only hang off a `.0`.** Alphas and release candidates exist for the next minor or major. A stable patch goes out directly with no prerelease, because it is a small, already-reviewed fix on a validated train. Alpha hotfixes use `alpha.N.M` and remain prereleases. If a stable patch feels risky enough to want an rc, it belongs in the next train instead.
- **New alpha lines start from `main`.** Before freeze, cut each `1.X.0-alpha.N` on `release/1.X.0-alpha.N` from a merged `main` commit. Version bumps and tags stay on that branch. Once a train freezes, `main` continues toward the next train; cutting a release does not advance `main`.
- **Alpha hotfixes reuse their alpha branch.** Cherry-pick fixes from `main` onto `release/1.X.0-alpha.N` and tag successive `alpha.N.M` releases there. For example, `1.1.0-alpha.1.1` and `1.1.0-alpha.1.2` both belong on `release/1.1.0-alpha.1`; never create a branch per hotfix. These hotfixes require a published alpha on that line, not a stable release. Both alpha hotfixes and stable patches exclude new migrations, database-schema changes, backfills, and new registry actions, including dependencies of a proposed fix.
- **One long-lived branch per frozen train.** Create `release/1.X` at freeze. It carries release candidates, the stable tag, and every `1.X.Y`. It is never deleted, and it is never merged back; fixes land on `main` first and are cherry-picked onto the branch. A patch reuses the existing train branch, rather than branching from newer `main` code.
- **Alpha releases are deployed to Tracecat’s own environments ahead of stable releases.** Self-hosted deployments should use stable version tags.
- **Breaking changes decide the next number.** A `!` in a pull request title marks a change that breaks a self-hoster without a deprecation path, such as dropping a migration chain, changing the Compose topology in a way that needs manual steps, or removing an API without the three-step deprecation above. Any such change in a train makes it the next major. Deprecations announced under that process ride minors.

### Cutting a release

Stable releases, prereleases, and hotfixes all use the same direct branch flow. The [gh-release skill](.agents/skills/gh-release/SKILL.md) covers the checks and commands. Release branches are not merged into `main`, and no release pull request is needed.

1. Choose a validated base commit. For a new alpha line, create its branch from merged `main`. For an alpha hotfix, reuse that alpha line's branch with reviewed fixes cherry-picked from `main`. At freeze, create the train branch; for subsequent RCs and stable patches, reuse its current tip with the reviewed fixes already cherry-picked. The branch must contain the current image-publishing workflow, including the stable-only `latest` guard.
2. On that branch, run `just update-version <version>`, review the diff, and commit it as `release: <version>`. The command writes the public tag to `__version__` and the PEP 440 equivalent (`1.1.0-alpha.1` becomes `1.1.0a1`) to `__pep440_version__` for Python package builds.
3. Push the branch, then create and push an annotated version tag on that version-bump commit. Never move an existing release tag. Leave the branch in place.
4. Wait for `.github/workflows/build-push-images.yml` to succeed for that tag and commit. It publishes `ghcr.io/tracecathq/tracecat:<tag>` and `ghcr.io/tracecathq/tracecat-ui:<tag>`. Only bare stable versions (`MAJOR.MINOR.PATCH`) also update `latest`; alphas, RCs, and nightly builds leave it unchanged. Every stable publication qualifies, including a hotfix on an older train.
5. Publish the GitHub release for the existing tag after verifying both images. Use `gh release create --verify-tag`, adding `--prerelease --latest=false` for an alpha or RC, or `--latest` for a stable release.

For a manual image rebuild, run `uv run python scripts/rebuild_release_images.py <tag>` from a trusted, updated `main` checkout. The command compares the workflow stored in the remote tag with the committed publisher in your checkout before dispatching with that tag as both ref and input. A different or missing workflow is rejected, including historical publishers that could promote prereleases to `latest`. Even a harmless workflow difference requires review; this check deliberately requires an exact match. Do not bypass a rejection with a direct dispatch or rerun, or move an existing tag. Prepare a new release containing the current publisher instead.

Release notes cover the range from the previous published version of the same train, or from the preceding stable version for the first release of a new train. They use the sections and formatting in `.github/release-drafter.yml`. Generate notes for the exact tag range; the draft maintained on `main` may contain changes outside the release branch.

## License

This project is licensed under the open source, copyleft [GNU Affero General Public License v3.0](LICENSE).

By contributing to this project, you agree that your contributions will be licensed under the same license.

Thank you for taking the time to contribute to Tracecat!
