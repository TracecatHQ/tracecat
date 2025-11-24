# Repository Guidelines

## Project Structure & Module Organization
- `tracecat/`: Python backend services, Temporal workers, and expression engine utilities.
- `packages/tracecat-registry/`: Registry package and YAML action templates; templates live under `tracecat_registry/templates/`.
- `frontend/`: Next.js app for the UI; uses `pnpm`.
- `tests/`: Python suites (`tests/unit`, `tests/registry`, `tests/playbooks`); mirror module paths when adding coverage.
- `docs/`, `deployments/`, `playbooks/`, `scripts/`: Documentation, deployment assets, runnable playbook examples, and helper scripts.

## Build, Test, and Development Commands
- Install deps: `uv sync` (installs the dev group from `uv.lock`; ensure Python 3.12 is available via `uv python install 3.12` if needed).
- Run dev stack: `docker compose -f docker-compose.dev.yml up -d` (UI on http://localhost); rebuild after dependency changes with `docker compose -f docker-compose.dev.yml build --no-cache`.
- Backend lint/format: `uv ruff check .` and `uv ruff format .`.
- Backend tests: `uv run pytest tests/unit tests/registry` (add `tests/playbooks` for end-to-end playbook coverage).
- Frontend: `pnpm -C frontend dev` for local UI; `pnpm -C frontend lint` to lint; `pnpm -C frontend test` if adding UI tests.
- Convenience: `just lint`, `just lint-fix`, `just dev`, `just test` mirror the above.

## Coding Style & Naming Conventions
- Python: 4-space indent, type hints for new code, favor dataclasses/pydantic models already used in the module. Keep functions small and pure where possible.
- Ruff is the source of truth for style and import ordering; run `uv ruff format` before committing.
- YAML templates: lower_snake_case for `expects` inputs; `title` <5 words, `namespace` like `tools.integration`.
- Branch names: `{feat|fix}/{short-description}` (e.g., `feat/jamf-lock-device-template`).

## Testing Guidelines
- Add or update tests alongside code; name files `test_*.py` under the corresponding subtree.
- Prefer fast unit tests; keep integration or playbook tests tagged and selective.
- When adding inline functions or templates, include registry tests that exercise both success and failure paths.
- Record key test commands in the PR (e.g., `uv run pytest tests/unit`).

## Commit & Pull Request Guidelines
- Commits: short imperative summaries; optional scope prefix (`feat:`, `fix:`, `chore:`) when helpful. Keep one logical change per commit.
- PRs: link related issue, describe user impact and rollout risks, list test commands run, and attach screenshots/GIFs for UI changes.
- Keep PRs focused; avoid mixing refactors with feature work unless required.

## Security & Configuration Tips
- Never commit secrets; prefer `.env` files kept local. Rotate keys if exposed.
- After touching `pyproject.toml` or `registry/pyproject.toml`, rebuild the dev stack (`docker compose -f docker-compose.dev.yml down` then `... build --no-cache up -d`).
- Validate new templates against the documented schema before submission to avoid runtime failures.
