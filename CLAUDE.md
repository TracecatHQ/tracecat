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

IMPORTANT: Before using `just cluster`, ALWAYS check for an existing docker compose stack first:
```bash
docker compose ls --filter name=tracecat
```
If a stack called `tracecat` is already running, ask the user whether they want to:
1. Use `docker compose` directly to interact with the existing stack
2. Use `just cluster` instead to manage development stacks

CRITICAL: Do not delete or remove docker volumes (`docker compose down -v`, `docker volume rm`, `just cluster rm`, etc.) unless the user explicitly requests AND confirms volume deletion. Volumes contain database state and other persistent data that cannot be recovered.

```bash
# Use `just cluster` to manage the development stack
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

### Testing
```bash
# Run all tests
just test
# Or manually: pytest --cache-clear tests/registry tests/unit tests/playbooks -x

# Run specific test suites
uv run pytest tests/unit          # Fast, isolated unit tests
uv run pytest tests/integration  # Integration tests (requires live services)
uv run pytest tests/registry     # Registry/integration tests
uv run pytest tests/unit/test_functions.py -x --last-failed  # Inline functions tests

# Parallel execution with pytest-xdist
uv run pytest tests/unit -n auto

# Run tests matching a keyword
uv run pytest -k "keyword"

# Run with specific markers
uv run pytest -m "not slow and not temporal"
uv run pytest -m temporal  # Temporal/workflow tests only

# Run benchmarks (requires Docker for nsjail on macOS)
just bench

# Frontend tests
cd frontend && pnpm test
```

### Temporal Management
```bash
# Stop all running Temporal workflow executions
just temporal-stop-all
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

### Pre-push Verification
**IMPORTANT**: Always run these checks before pushing. Pre-commit hooks catch most issues, but you should verify locally if in doubt.
```bash
# Run all CI-equivalent checks (must all pass before pushing)
uv run ruff check .                          # Python lint (strict, no auto-fix)
uv run ruff format --check .                 # Python format check
uv run basedpyright --warnings --threads 4   # Python type checking
pnpm -C frontend check                      # Frontend lint + format (Biome)
pnpm -C frontend run typecheck              # TypeScript type checking
```

**Required before reporting "done"**: Always run the auto-fixers first, then re-run the checks above.
```bash
# Auto-fixers (run whenever you touch Python/TS/TSX)
uv run ruff check --fix .
pnpm -C frontend exec biome check --write .
```

**Recommended pre-push hook**: Prevent pushes unless auto-fix + checks pass.
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

**Pre-commit hooks**: Runs automatically on commit:
- Ruff (lint + format)
- Gitleaks (secret detection)
- YAML/TOML validation
- UV lock sync
- Frontend client generation (when tracecat/packages change)
- basedpyright (Python type checking)
- Frontend Biome check (lint + format on frontend changes)
- TypeScript type checking (on frontend changes)

**CI Requirements**: Both linting (`just fix`) and type checking (`just typecheck`) must pass in CI before merging.

### API and Code Generation
```bash
# Generate frontend API client
just gen-client-ci

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
- **Executor Service** (`tracecat/executor/`): Action execution engine with index-based registry resolution
- **Agent System** (`tracecat/agent/`): LLM-powered agents with multi-runtime support (PydanticAI, Claude Code), MCP integration, tool execution
- **Organization Service** (`tracecat/organization/`): Multi-tenancy and organization membership management
- **tracecat-admin CLI** (`packages/tracecat-admin/`): CLI tool for platform operators (admin, auth, migrate, orgs, registry commands)
- **Frontend** (`frontend/`): Next.js 15 with TypeScript, React Query, Tailwind CSS
- **Registry** (`packages/tracecat-registry/`): Independent package for integrations and templates

### Key Technologies
- **Backend**: FastAPI, SQLAlchemy, Pydantic, Temporal, Ray, PostgreSQL, Alembic, PydanticAI, LiteLLM, FastMCP
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

### Agent System
- **Location**: `tracecat/agent/`
- **Purpose**: LLM-powered agents for workflow automation
- **Key directories**: `runtime/` (PydanticAI, Claude Code), `mcp/` (Model Context Protocol), `executor/`, `preset/`

## Development Guidelines

### Dependency Management and Security
- **Always pin exact versions** in `pyproject.toml` (e.g., `package==1.2.3` not `package>=1.2.3`) to prevent supply chain attacks
- When resolving merge conflicts in dependencies, ensure exact version pins are preserved
- Security fixes should update the pinned version to the specific patched version, not use range constraints

### Python Standards
- Use Python 3.12+ type hints with builtin types (`list`, `dict`, `set`)
- Follow Google Python style guide
- Import statements at top of file only
- Use `uv run` for executing Python/pytest commands
- Use `uv pip install` for package installation
- Test directories: `tests/unit/` (fast, isolated unit tests), `tests/integration/` (live services, no mocks), `tests/temporal/` (Temporal workflows), `tests/registry/` (registry/integrations), `tests/llm/` (LLM calls), `tests/regression/` (regression tests), `tests/stress/` and `tests/load/` (performance)
- `tests/unit/` should be fast and isolated — mocks are acceptable here
- `tests/integration/` should test against real services with no mocks, as close to production as possible
- Always use `@pytest.mark.anyio` in async python tests over `@pytest.mark.asyncio`
- Always avoid use of `type: ignore` when writing python code
- You must *NEVER* put import statements in function bodies.
- If you are facing issues with circular imports you should try use `if TYPE_CHECKING: ...` instead.
- Use PEP 695 generic syntax for new generics: `class Name[T: Bound]` over `TypeVar`
- Use `StrEnum` for string-based enumerations (JSON/YAML serialization)
- Use `frozen=True` dataclasses for immutable value objects
- Use `TypedDict` with `NotRequired` for configuration types
- Use `@runtime_checkable` protocols for structural typing
- Avoid adding re-exports to `__init__.py` files; import directly from submodules (e.g., `from tracecat.agent.schemas import RunAgentArgs` not `from tracecat.agent import RunAgentArgs`). This keeps imports explicit, avoids circular import issues, and improves import performance. Exception: re-exports make sense for versioned external packages where you need to hide internal structure—rare for internal code.

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

### Service Architecture
Services inherit from `BaseService` (`tracecat/service.py`) which provides:
- Automatic logger binding with service name
- Context-aware role fallback via `ctx_role.get()`
- `with_session()` context manager for lifecycle management

Context variables (`tracecat/contexts.py`) for request-scoped state:
- `ctx_role`: Current user/service role
- `ctx_run`: Workflow run context
- `ctx_logger`: Request-scoped logger
- `ctx_interaction`: Interaction context for workflows

Router access control using predefined roles from `tracecat/auth/dependencies.py`:
```python
from tracecat.auth.dependencies import WorkspaceUserRole, OrgAdminUser

@router.get("/endpoint")
async def handler(role: WorkspaceUserRole):  # User with workspace access
    ...

@router.get("/admin")
async def admin_handler(role: OrgAdminUser):  # Org admin required
    ...
```

Available predefined roles:
- `WorkspaceUserRole`: User with workspace access
- `ExecutorWorkspaceRole`: Executor service with workspace
- `ServiceRole`: Internal service role
- `OrgAdminUser`: Organization admin user

### Pagination
- **MUST use cursor-based pagination** for all list/search endpoints that can return multiple records. Do not introduce offset/page-number pagination for new APIs.
- Use `CursorPaginationParams` (or the module's equivalent cursor schema) as the input contract and return a typed paginated response with `items` and `next_cursor`.
- Keep routes idiomatic: collection endpoints stay on the base resource path (for example, `/items`, `/cases`, `/workflows`). Do not add `/paginated` suffix routes.
- Avoid duplicate APIs for the same behavior (for example, `list_*` + `list_*_paginated`). Keep one canonical list/search implementation per resource.
- Service methods should expose a single paginated list/search entrypoint. If both `list_*` and `search_*` exist with identical behavior, make one call the other instead of duplicating query logic.
- Enforce `limit` bounds consistently at both route and schema level (`Query(ge=..., le=...)` plus Pydantic field constraints), using shared config constants from `tracecat/config.py`.
- Cursor contracts must be stable: sort order and cursor encoding/decoding must produce deterministic pagination without missing or duplicate rows.

### Frontend Standards
- Use kebab-case for file names
- Use camelCase for functions/variables, UPPERCASE_SNAKE_CASE for constants
- Prefer `function foo()` over `const foo = () =>`
- Use named exports over default exports
- Use "Title case example" over "Title Case Example" for UI text
- Always use proper TypeScript type hints and avoid using `any` - use `unknown` if necessary
- Avoid nested ternary statements - use `if/else` or `switch/case` instead
- Place React hooks in `frontend/src/hooks/` directory (e.g., `use-inbox.ts`, `use-auth.ts`)
- For keyboard shortcut UI, render each key with the `Kbd` component and prefer `parseShortcutKeys` from `frontend/src/lib/tiptap-utils.ts` to ensure consistent macOS symbols (`⌘`, `⇧`) and non-mac labels (`Ctrl`, `Shift`).

### UI Component Best Practices
- **Flat, Linear-inspired design**: Follow a minimal, flat design aesthetic inspired by Linear. Key principles:
  - **No shadows**: Keep designs flat and avoid using shadows unless explicitly asked for.
  - **No nested containers**: Avoid putting cards/containers inside other containers. This creates unnecessary visual clutter.
  - **Neutral colors only**: Use grayscale/neutral palette unless explicitly asked for color. Avoid colored buttons - prefer neutral variants unless explicitly asked for a color.
- **Avoid background colors on child elements within bordered containers**: When using shadcn components like SidebarInset that have rounded borders, don't add background colors (e.g., `bg-card`, `bg-background`) to immediate child elements. These backgrounds can paint over the parent's rounded border corners, making them appear cut off or missing. Instead, let the parent container handle the background styling.
- **Standard settings/admin page layout**: All settings and admin pages must use this layout pattern for consistency:
  ```tsx
  <div className="size-full overflow-auto">
    <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
      <div className="flex w-full">
        <div className="items-start space-y-3 text-left">
          <h2 className="text-2xl font-semibold tracking-tight">Title</h2>
          <p className="text-base text-muted-foreground">Subtitle</p>
        </div>
        {/* Optional: action buttons on the right */}
        {/* <div className="ml-auto flex items-center space-x-2">...</div> */}
      </div>
      {/* Page content */}
    </div>
  </div>
  ```
  Key rules: outer `size-full overflow-auto` wrapper, inner container with `max-w-[1000px]`, `space-y-12` section spacing, `h2` for page title, `text-base` on subtitle, `space-y-3` title-subtitle gap. For pages with a back link, place it above the `flex w-full` header div.

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
- `tracecat/service.py`: Base service class with context-aware defaults
- `tracecat/contexts.py`: Context variables for request-scoped state
- `scripts/cluster`: Multi-cluster orchestration script

### Testing Patterns
- **Test directories**:
  - `tests/unit/` — Fast, isolated unit tests (mocks OK)
  - `tests/integration/` — Live service tests, no mocks (requires `just cluster up -d`)
  - `tests/temporal/` — Temporal workflow tests
  - `tests/registry/` — Registry and integration tests
  - `tests/llm/` — LLM call tests
  - `tests/regression/` — Regression tests
  - `tests/stress/`, `tests/load/` — Performance tests
  - `tests/backends/` — Backend-specific tests
- `tests/conftest.py`: Comprehensive pytest fixtures for database, workspaces, temporal
- Test markers: `@pytest.mark.integration`, `@pytest.mark.unit`, `@pytest.mark.slow`, `@pytest.mark.temporal`, `@pytest.mark.llm`
- Database isolation: Each test gets its own transaction
- **Parallel testing**: pytest-xdist support with worker-specific:
  - Temporal task queues: `tracecat-task-queue-{worker_id}`
  - Redis databases: Worker offset % 16
  - Port configuration via environment variables

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
- **NEVER** use `--no-gpg-sign` or `--no-verify` to bypass commit signing. If GPG/SSH signing fails (e.g., 1Password agent not running), stop and ask the user to fix their signing setup rather than creating an unverified commit.

- For infrastructure changes, always verify and update all relevant deployment targets together: `docker-compose*.yml`, Terraform Fargate (`deployments/fargate/`), Terraform EKS (`deployments/aws/` and `deployments/aws/modules/eks/`, or `deployments/terraform/aws/` and `deployments/terraform/aws/modules/eks/` in this repo layout), and Helm (`deployments/helm/`).
- As part of that infra review, explicitly check `values.yaml`, `variables.tf`, and `main.tf` in the relevant deployment directories before marking the change complete.

## Pull Request Description Hygiene
- Never use `gh pr create --body "..."` when the body includes Markdown or backticks.
- Always write the PR body to a file using a single-quoted heredoc (`<<'EOF'`) and pass it with `gh pr create --body-file <file>`.
- After creating or editing a PR body, always verify with `gh pr view <pr-number> --json body --jq .body` and confirm inline code, endpoint paths, and backticks are preserved exactly.
- If formatting is wrong, immediately fix it with `gh pr edit <pr-number> --body-file <file>` and re-verify.
- Always keep auto-generated PR content from cubic; do not remove or replace it unless the user explicitly asks.

## Code Typing Guidelines
- When writing typescript code, always do your best to use proper type hints and avoid using `any`. If you really have to you can use `unknown`.

## Frontend Type Generation
- If you need to add frontend types, you should first try to generate them from the backend using `just gen-client-ci`

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
