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

We follow the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) specification for both pull requests and commit messages.

Your pull request title and commit message should have the following format:

```txt
<type>[optional scope]: <description>
```

Types may include:

* `feat`: A new feature
* `fix`: A bug fix
* `docs`: Documentation-only changes
* `style`: Changes that do not affect the meaning of the code, such as whitespace or formatting
* `refactor`: A code change that neither fixes a bug nor adds a feature
* `perf`: A code change that improves performance
* `test`: Adding missing tests or correcting existing tests
* `chore`: Other changes that do not modify source or test files

## Release Process

Tracecat loosely follows the [Semantic Versioning](https://semver.org/) specification for releases.

We are currently on the `1.0.0-beta.xyz` version series.

## License

This project is licensed under the open source, copyleft [GNU Affero General Public License v3.0](LICENSE).

By contributing to this project, you agree that your contributions will be licensed under the same license.

Thank you for taking the time to contribute to Tracecat!
