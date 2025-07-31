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
just lint-fix-ui     # Frontend: pnpm check (Biome lint, format, and organize imports)

# Frontend-specific Biome commands
cd frontend && pnpm lint          # Biome lint
cd frontend && pnpm format:write  # Biome format
cd frontend && pnpm check         # Biome comprehensive check (lint + format + organize imports)

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
- Always use `@pytest.mark.anyio` in async python tests over `@pytest.mark.asyncio`
- Always avoid use of `type: ignore` when writing python code

### Frontend Standards
- Use kebab-case for file names
- Use camelCase for functions/variables, UPPERCASE_SNAKE_CASE for constants
- Prefer `function foo()` over `const foo = () =>`
- Use named exports over default exports
- Use "Title case example" over "Title Case Example" for UI text

### UI Component Best Practices
- **Avoid background colors on child elements within bordered containers**: When using shadcn components like SidebarInset that have rounded borders, don't add background colors (e.g., `bg-card`, `bg-background`) to immediate child elements. These backgrounds can paint over the parent's rounded border corners, making them appear cut off or missing. Instead, let the parent container handle the background styling.

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
- **Reference file**: `tracecat/expressions/expectations.py` – Source of primitive type mappings (e.g., `str`, `int`, `Any`) used when defining `expects:` sections in templates.
- **Naming**: `tools.{integration_name}` namespace, titles < 5 words

### Template Best Practices
- **URL Encoding**: Use `${{ FN.url_encode(inputs.param) }}` when interpolating user inputs into URL paths (especially for IDs that might be emails)
- **Type Syntax**: Use `str | None` instead of `str | null` for optional types
- **GET Requests**: Don't include `Content-Type` header on GET requests
- **Optional Parameters**: Use `core.script.run_python` to conditionally build params dict, excluding null values
- **Response Format**: Return `${{ steps.step_name.result.data }}` directly, avoid custom response formatting
- **Error Handling**: Let the platform handle HTTP errors, don't add custom error checking unless necessary

### Workflow and Execution
- **DSL**: `tracecat/dsl/` - Domain Specific Language for workflows
- **Executor**: `tracecat/executor/` - Action execution engine with Ray distributed computing
- **Temporal**: Workflow orchestration with `tracecat/dsl/worker.py`

## Important Rules
- Never add methods in `tracecat/db/schemas.py`. Keep imports minimal.
- Always use `pnpm` over `npm` and `rg` instead of `grep`
- Always ask clarifying questions when lacking full context
- When handling frontend types, don't import variables prefixed with '$' unless you are importing the schema object

## Code Typing Guidelines
- When writing typescript code, always do your best to use proper type hints and avoid using `any`. If you really have to you can use `unknown`.

## Code Style Guidelines
- When writing typescript code, always avoid using nested ternary statements. You probably want to use `if/else` or `switch/case`.

## Frontend Type Generation
- If you need to add frontend types, you should first try to generate them from the backend using `just gen-client`

## Database Migrations
- When running an alembic migration, you should use `export TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres` or pass it into the command

## Services and Logging Guidelines
- When working with live services, avoid using `just` commands. You should use `docker` commands to inspect/manage services and read logs.

## Tracecat Template Best Practices

### Template Structure
- Templates are YAML files located in `registry/tracecat_registry/templates/`
- Use namespace pattern `tools.{integration_name}` (e.g., `tools.zendesk`, `tools.okta`)
- Action titles should be < 5 words and use "Title case example" format

### Expression Syntax
- Use `${{ }}` for template expressions
- Boolean operators: Use `||` for OR, `&&` for AND (not Python's `or`/`and`)
- String casting: `str()` is valid in Tracecat templates
- All FN. functions must exist in `tracecat/expressions/functions.py`

### Available Functions (FN.)
IMPORTANT: Always check `@tracecat/expressions/functions.py` for the complete and up-to-date list of available functions. Look at the `_FUNCTION_MAPPING` dictionary (around line 905) to see all available functions and their aliases.

When writing templates, ensure that any FN. function you use exists in the `_FUNCTION_MAPPING` dictionary. The function names in templates must match the dictionary keys exactly.

### HTTP Requests
- Use `core.http_request` action
- Parameter name for JSON body is `payload` (not `json` or `json_body`)
- Example:
  ```yaml
  - ref: call_api
    action: core.http_request
    args:
      url: https://api.example.com/endpoint
      method: POST
      headers:
        Authorization: Bearer ${{ SECRETS.api.TOKEN }}
      payload:  # Correct parameter name
        key: value
  ```

### Complex Logic
- For simple conditions: Use template expressions with `||` and `&&`
  ```yaml
  value: ${{ inputs.login || inputs.email }}
  ```
- For complex logic: Use `core.script.run_python`
  ```yaml
  - ref: complex_logic
    action: core.script.run_python
    args:
      inputs:
        data: ${{ inputs.data }}
      script: |
        def main(data):
            # Complex processing here
            return processed_data
  ```

### Best Practices
1. **Avoid Python syntax in expressions**: Use `||` not `or`, `&&` not `and`
2. **Use Python scripts for complexity**: Dictionary merging, complex conditionals, data transformations
3. **Validate function names**: Ensure all FN. functions exist in expressions/functions.py
4. **Consistent namespaces**: Always use `tools.` prefix for integrations
5. **Proper parameter names**: Use `payload` for JSON data in HTTP requests
6. **Handle empty responses**: Some APIs return empty responses on success
7. **Error handling**: Consider using Python scripts to handle edge cases

### Common Patterns
- **Authentication headers**:
  ```yaml
  Authorization: Basic ${{ FN.to_base64(SECRETS.creds.USERNAME + ":" + SECRETS.creds.PASSWORD) }}
  Authorization: Bearer ${{ SECRETS.api.TOKEN }}
  ```
- **Conditional values**:
  ```yaml
  value: ${{ inputs.override || defaults.standard_value }}
  ```
- **Complex data merging**:
  ```yaml
  - ref: merge_data
    action: core.script.run_python
    args:
      inputs:
        base: ${{ inputs.base_data }}
        updates: ${{ inputs.updates }}
      script: |
        def main(base, updates):
            return {**base, **updates}
  ```
