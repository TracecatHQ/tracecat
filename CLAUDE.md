# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
Tracecat is a modern, open source automation platform built for security and IT engineers. No-code UI workflows, built-in lookup tables, case management, and Temporal orchestration.

## Development Commands

### Environment Setup
```bash
# Create Python 3.12 virtual environment and install from lock file
# This is a workspace project - run from anywhere inside the repo
# (includes both tracecat and tracecat_registry packages)
uv sync

# Install frontend dependencies
pnpm install --dir frontend

# Install pre-commit hooks
uv run pre-commit install
```

### Updating Dependencies
```bash
# Update dependencies and regenerate lock file
# Lock file updates are automatic if you delete uv.lock and run uv sync
rm uv.lock && uv sync

# Or manually compile a new lock file
uv pip compile pyproject.toml -o uv.lock

# Install updated dependencies
uv sync
```

### Development Stack
```bash
# IMPORTANT: Always use `just cluster` to manage the development stack
# This handles database, Temporal, Redis, MinIO, API, and UI services
# It also manages port allocation across multiple worktrees

# Start the full stack (auto-selects available cluster number)
just cluster up -d

# Start with auto-registered test user (test@tracecat.com / password1234)
just cluster up -d --seed

# View all available commands
just cluster

# Common commands:
just cluster ps              # Show running containers
just cluster logs api        # View API service logs
just cluster logs -f api     # Follow API logs
just cluster restart api     # Restart a service (hot reload)
just cluster down            # Stop the stack (keeps volumes)
just cluster rm              # Stop and remove volumes
just cluster attach api      # Shell into a container
just cluster db              # Open TablePlus to PostgreSQL
just cluster ports           # Show port mappings
just cluster list            # List all running clusters

# Port mappings (cluster 1 defaults):
# - App UI: http://localhost:80
# - API: http://localhost:80/api
# - PostgreSQL: localhost:5432
# - Temporal UI: http://localhost:8081
```

**When to use `just cluster`:**
- Need a database connection → `just cluster up -d`
- Need to run integration tests → `just cluster up -d`
- Need Temporal for workflow testing → `just cluster up -d`
- Need to check service logs → `just cluster logs <service>`
- Need to restart after code changes → `just cluster restart <service>`

**Do NOT use raw `docker` or `docker compose` commands** - the cluster script handles environment variables, port allocation, and worktree isolation automatically.

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
# Lint and format everything (run before committing)
just fix             # Short alias
just lint-fix        # Same as above

# Individual components
just lint-fix-app    # Python: ruff check --fix . && ruff format .
just lint-fix-ui     # Frontend: pnpm check --write (Biome lint, format, and organize imports)

# Check only (no auto-fix) - useful for CI or verifying changes
uv run ruff check .              # Python lint check only
uv run ruff format --check .     # Python format check only
cd frontend && pnpm check        # Frontend check only

# Frontend-specific Biome commands
cd frontend && pnpm lint          # Biome lint
cd frontend && pnpm format:write  # Biome format
cd frontend && pnpm check         # Biome comprehensive check (lint + format + organize imports)
```

### Type Checking
```bash
# Run basedpyright type checking (required before merging)
just typecheck

# Or run directly with options
uv run basedpyright --warnings --threads 4

# Check specific files or directories
uv run basedpyright tracecat/api/

# Common type errors to avoid:
# - Using `type: ignore` comments (find alternative solutions)
# - Missing return type annotations on public functions
# - Using `Any` when a more specific type is possible
```

**Pre-commit hooks**: Runs automatically on commit (Ruff, Gitleaks secret detection, YAML/TOML validation).

**CI Requirements**: Both linting (`just fix`) and type checking (`just typecheck`) must pass in CI before merging.

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
- **Backend**: FastAPI, SQLAlchemy, Pydantic, Temporal, Ray, PostgreSQL, Alembic
- **Frontend**: Next.js 15, TypeScript, React Query, Tailwind CSS, Radix UI
- **Infrastructure**: Docker, PostgreSQL, MinIO, Temporal Server
- **Package Management**: `uv` for Python, `pnpm` for JavaScript

### Database and Migrations
- **Database Models**: `tracecat/db/models.py` - SQLAlchemy database tables. Never add methods here, keep imports minimal
- **Migrations**: `alembic/` directory with comprehensive schema evolution
- **Database Engine**: `tracecat/db/engine.py` for connection management

### Type System Architecture
The codebase follows a three-tier type system to separate concerns and reduce circular imports:

1. **`models.py`**: Database models (SQLAlchemy tables)
   - Location: `tracecat/db/models.py`
   - Purpose: Database table definitions
   - Rules: Never add methods, keep imports minimal

2. **`schemas.py`**: API request/response schemas (Pydantic models)
   - Location: Throughout codebase (e.g., `tracecat/agent/schemas.py`, `tracecat/workflow/management/schemas.py`)
   - Purpose: API contracts, DTOs, request/response models
   - Naming: Previously called `models.py`, renamed for clarity

3. **`types.py`**: Domain types, protocols, and type aliases
   - Location: Throughout codebase (e.g., `tracecat/agent/types.py`, `tracecat/workflow/management/types.py`)
   - Purpose: Service-level types, protocols, dataclasses, type aliases
   - Use: Domain logic types that don't fit in schemas or models

### Enterprise Edition
- **Package**: `packages/tracecat-ee/` contains paid enterprise features
- **Installation**: Install with `uv sync` or `pip install tracecat[ee]`
- **Shims**: `tracecat/ee/` contains shims for backward compatibility
- **Features**: RBAC, multi-tenancy, SSO integration, advanced auth, interactions

## Development Guidelines

### Dependency Management and Security
- **Always pin exact versions** in `pyproject.toml` (e.g., `package==1.2.3` not `package>=1.2.3`) to prevent supply chain attacks
- When resolving merge conflicts in dependencies, ensure exact version pins are preserved
- Security fixes should update the pinned version to the specific patched version, not use range constraints

### Python Standards
- Use Python 3.11+ type hints with builtin types (`list`, `dict`, `set`)
- Follow Google Python style guide
- Import statements at top of file only
- Use `uv run` for executing Python/pytest commands
- Use `uv pip install` for package installation
- Tests under `tests/unit` are integration tests - no mocks, test as close to production as possible
- Always use `@pytest.mark.anyio` in async python tests over `@pytest.mark.asyncio`
- Always avoid use of `type: ignore` when writing python code
- You must *NEVER* put import statements in function bodies.
- If you are facing issues with circular imports you should try use `if TYPE_CHECKING: ...` instead.

### Type Organization Guidelines
When adding new types, follow this pattern:
- **Database tables**: Add to `tracecat/db/models.py` (SQLAlchemy classes)
- **API schemas**: Add to module-specific `schemas.py` files (Pydantic models for request/response)
- **Domain types**: Add to module-specific `types.py` files (protocols, dataclasses, type aliases)
- **Avoiding circular imports and improving import performance**: Use `if TYPE_CHECKING:` for type-only imports, move shared types to `types.py`
- **Import order**: `models` → `types` → `schemas` → `service` → `router` (to minimize circular dependencies)

Example structure for a module like `tracecat/agent/`:
```
tracecat/agent/
├── schemas.py       # API request/response models (RunAgentArgs, AgentStreamChunk)
├── types.py         # Domain types (AgentConfig, StreamingAgentDeps protocol)
├── service.py       # Business logic
└── router.py        # FastAPI routes
```

### Frontend Standards
- Use kebab-case for file names
- Use camelCase for functions/variables, UPPERCASE_SNAKE_CASE for constants
- Prefer `function foo()` over `const foo = () =>`
- Use named exports over default exports
- Use "Title case example" over "Title Case Example" for UI text
- Always use proper TypeScript type hints and avoid using `any` - use `unknown` if necessary
- Avoid nested ternary statements - use `if/else` or `switch/case` instead
- Place React hooks in `frontend/src/hooks/` directory (e.g., `use-inbox.ts`, `use-auth.ts`)

### UI Component Best Practices
- **Avoid background colors on child elements within bordered containers**: When using shadcn components like SidebarInset that have rounded borders, don't add background colors (e.g., `bg-card`, `bg-background`) to immediate child elements. These backgrounds can paint over the parent's rounded border corners, making them appear cut off or missing. Instead, let the parent container handle the background styling.

### Code Quality
- **Ruff**: Line length 88, comprehensive linting rules
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
- **Templates**: `packages/tracecat-registry/tracecat_registry/templates/` - YAML-based integration templates
- **Integrations**: `packages/tracecat-registry/tracecat_registry/integrations/` - Python client integrations
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
- Never add methods in `tracecat/db/models.py`. Keep imports minimal.
- Always use `pnpm` over `npm` and `rg` instead of `grep`
- Always ask clarifying questions when lacking full context
- When handling frontend types, don't import variables prefixed with '$' unless you are importing the schema object

## Code Typing Guidelines
- When writing typescript code, always do your best to use proper type hints and avoid using `any`. If you really have to you can use `unknown`.

## Frontend Type Generation
- If you need to add frontend types, you should first try to generate them from the backend using `just gen-client`

## Database Migrations
- Ensure the database is running first: `just cluster up -d`
- When running an alembic migration, first check the PostgreSQL port with `just cluster ports`, then set the DB URI:
  ```bash
  export TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:<port>/postgres
  ```
  Default port is 5432 for cluster 1, but may be 5532, 5632, etc. for other clusters.
- **Creating new migrations**: Always let Alembic autogenerate the migration first to get correct naming conventions and structure:
  ```bash
  uv run alembic revision --autogenerate -m "description of migration"
  ```
  Then review and edit the generated migration file as needed. This ensures consistent revision IDs and proper down_revision chains.

## Services and Logging Guidelines
- When working with live services, use `just cluster` commands to manage the stack:
  - `just cluster logs <service>` - View service logs
  - `just cluster logs -f <service>` - Follow logs in real-time
  - `just cluster ps` - Check container status
  - `just cluster restart <service>` - Restart a service
  - `just cluster attach <service>` - Shell into a running container
- Do NOT use raw `docker` or `docker compose` commands directly - the cluster script handles environment variables and port allocation

## Tracecat Template Best Practices

### Template Structure
- Templates are YAML files located in `packages/tracecat-registry/tracecat_registry/templates/`
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

- when using typescript you must *never* use `any` to type a variable
- When adding any pages that require redirection/callbacks to the UI we need to create a NextJS route handler for that path
- When you write a commit message, you *MUST* use conventional commits standards. You must also *NEVER* use the `claude` scope for the commit message, as it is not informative.
