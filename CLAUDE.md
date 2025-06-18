# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
Tracecat is a modern, open source automation platform built for security and IT engineers. Alternative to Tines/Splunk SOAR with YAML-based templates, no-code UI workflows, built-in lookup tables, case management, and Temporal orchestration.

## Development Commands

### Environment Setup
```bash
# Create Python 3.12 virtual environment
uv venv --python 3.12

# Install main package and registry in development mode
uv pip install -e ".[dev]"
uv pip install -e "tracecat_registry[cli] @ ./registry"

# Install frontend dependencies
pnpm install --dir frontend

# Install pre-commit hooks
uv pip install pre-commit
uv run pre-commit install
```

### Development Stack
```bash
# Start development environment
just dev
# Or manually: docker compose -f docker-compose.dev.yml up

# Rebuild development stack (after dependency changes)
just build-dev
# Or manually: docker compose -f docker-compose.dev.yml build --no-cache

# Access UI at http://localhost
```

### Testing
```bash
# Run all tests
just test
# Or manually: pytest --cache-clear tests/registry tests/unit tests/playbooks -x

# Run specific test suites
uv run pytest tests/unit          # Backend/API tests
uv run pytest tests/registry     # Registry/integration tests
uv run pytest tests/unit/test_functions.py -x --last-failed  # Inline functions tests

# Frontend tests
cd frontend && pnpm test
```

### Linting and Formatting
```bash
# Lint and format everything
just lint-fix

# Individual components
just lint-fix-app    # Python: ruff check . && ruff format .
just lint-fix-ui     # Frontend: pnpm lint:fix && pnpm format:write

# Type checking
just mypy <path>     # MyPy type checking for specific path
```

### API and Code Generation
```bash
# Generate frontend API client
just gen-client

# Generate API spec (requires CLI installed)
just gen-api

# Generate integrations and functions (requires CLI installed)
just gen-integrations
just gen-functions
```

## Architecture Overview

### Core Components
- **API Service** (`tracecat/api/`): FastAPI application with auth, workflows, cases
- **Worker Service** (`tracecat/dsl/worker.py`): Temporal workflow worker
- **Executor Service** (`tracecat/api/executor.py`): Action execution engine
- **Frontend** (`frontend/`): Next.js 15 with TypeScript, React Query, Tailwind CSS
- **Registry** (`registry/`): Independent package for integrations and templates

### Key Technologies
- **Backend**: FastAPI, SQLModel, Pydantic, Temporal, Ray, PostgreSQL, Alembic
- **Frontend**: Next.js 15, TypeScript, React Query, Tailwind CSS, Radix UI
- **Infrastructure**: Docker, PostgreSQL, MinIO, Temporal Server
- **Package Management**: `uv` for Python, `pnpm` for JavaScript

### Database and Migrations
- **Schema**: `tracecat/db/schemas.py` - Never add methods here, keep imports minimal
- **Migrations**: `alembic/` directory with comprehensive schema evolution
- **Database Engine**: `tracecat/db/engine.py` for connection management

### Enterprise Edition
- **Location**: `tracecat/ee/` directory contains paid enterprise features
- **Features**: RBAC, multi-tenancy, SSO integration, advanced auth

## Development Guidelines

### Python Standards
- Use Python 3.11+ type hints with builtin types (`list`, `dict`, `set`)
- Follow Google Python style guide
- Import statements at top of file only
- Use `uv run` for executing Python/pytest commands
- Use `uv pip install` for package installation
- Tests under `tests/unit` are integration tests - no mocks, test as close to production as possible

### Frontend Standards
- Use kebab-case for file names
- Use camelCase for functions/variables, UPPERCASE_SNAKE_CASE for constants
- Prefer `function foo()` over `const foo = () =>`
- Use named exports over default exports
- Use "Title case example" over "Title Case Example" for UI text

### Code Quality
- **Ruff**: Line length 88, comprehensive linting rules
- **MyPy**: Strict type checking mode
- **Pre-commit**: Automated hooks for Ruff, Gitleaks, YAML/TOML validation
- All tests must pass before commits

## Key Files and Patterns

### Configuration Files
- `pyproject.toml`: Main Python project config with dependencies
- `frontend/package.json`: Frontend dependencies and scripts
- `docker-compose.dev.yml`: Development environment
- `alembic.ini`: Database migration configuration

### Testing Patterns
- `tests/conftest.py`: Comprehensive pytest fixtures for database, workspaces, temporal
- Test markers: `@pytest.mark.integration`, `@pytest.mark.unit`, `@pytest.mark.slow`
- Database isolation: Each test gets its own transaction

### Action Templates and Registry
- **Templates**: `registry/tracecat_registry/templates/` - YAML-based integration templates
- **Schemas**: `registry/tracecat_registry/schemas/` - Response schemas for consistent APIs
- **Integrations**: `registry/tracecat_registry/integrations/` - Python client integrations
- **Naming**: `tools.{integration_name}` namespace, titles < 5 words

### Workflow and Execution
- **DSL**: `tracecat/dsl/` - Domain Specific Language for workflows
- **Executor**: `tracecat/executor/` - Action execution engine with Ray distributed computing
- **Temporal**: Workflow orchestration with `tracecat/dsl/worker.py`

## Important Rules
- Never add methods in `tracecat/db/schemas.py`. Keep imports minimal.
- Always use `pnpm` over `npm` and `rg` instead of `grep`
- Always ask clarifying questions when lacking full context
