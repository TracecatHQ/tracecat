# Contributing to Tracecat

Thank you for your interest in contributing to Tracecat!

## Before You Begin

Join our [Discord](https://discord.gg/H4XZwsYzY4) and `#contributors` channel to get started.
Also check out

## Feature Requests

Please open a [GitHub issue](https://github.com/TracecatHQ/tracecat/issues/new/choose) with the `Feature request` template.
Alternatively, you can join our Discord channel and discuss the feature request there with our community before opening an issue.

## Bug Reports

If you discover a bug, either:
- Open a [GitHub issue](https://github.com/TracecatHQ/tracecat/issues/new/choose) with the `Bug report` template
- Post a question in our Discord `#questions` channel

We will only accept bug reports that met the following criteria:
- Has a clear, descriptive title
- Has a clear reproductible example
- Clearly states Tracecat's version and what environment (local, VM, AWS, etc.) it was encountered in
- When this bug started occurring and was it ever working?

## Development Setup

> [!NOTE]
> Check our our [development setup guide](/docs/development-setup) in the docs for more information.

We use `docker compose` and the `docker-compose.dev.yml` files for development.

To set up your development environment, run:
```bash
just cluster up -d --seed
```

This starts the development environment and seeds a test user (`test@tracecat.com` / `password1234`).
You can then access the application at [http://localhost:80](http://localhost:80).

> [!IMPORTANT]
> `--seed` creates a test user only. Superadmin is determined by `TRACECAT__AUTH_SUPERADMIN_EMAIL` in `.env` (set via `./env.sh`), and the first signup/login with that email becomes the organization owner.

## PR and Commit Message Guidelines

We follow the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) specification for both pull requests and commit messages.
Your pull request title and commit message should have the following format:
```
<type>[optional scope]: <description>
```

Types may include:
- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation only changes
- `style`: Changes that do not affect the meaning of the code (white-space, formatting, missing semi-colons, etc)
- `refactor`: A code change that neither fixes a bug nor adds a feature
- `perf`: A code change that improves performance
- `test`: Adding missing tests or correcting existing tests
- `chore`: Other changes that don't modify src or test files

## License

This project is licensed under the open source, copy left [GNU Affero General Public License v3.0](LICENSE).
By contributing to this project, you agree that your contributions will be licensed under the same license.

Thank you for taking the time to contribute to Tracecat!

## Release Process

Tracecat loosely follows the [Semantic Versioning](https://semver.org/) specification for releases.
We are currently on version `1.0.0-beta.xyz` series.
