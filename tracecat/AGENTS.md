# Backend agent notes

Use these rules for work in `tracecat/` and as the default backend convention
set for related Python packages in this repo.

## Python standards

- Target Python 3.12+ and use builtin generics such as `list[str]`.
- Follow Google-style docstrings for public functions, methods, and classes.
- Keep imports at module scope. Do not move imports into function bodies.
- Use `uv run` for Python commands and tests.
- Use `uv pip install` for package installation.
- Avoid `type: ignore`. If imports are cyclical, prefer `if TYPE_CHECKING:`.
- Prefer `frozen=True` dataclasses for immutable value objects.
- Prefer `TypedDict` for structured dictionaries and `Protocol` for structural
  typing.
- Use `TypedDict` with `NotRequired` for optional configuration keys.
- Use `@runtime_checkable` on protocols that need runtime structural checks.
- Use PEP 695 generics for new generic definitions.
- Prefer `StrEnum` or `Literal` over plain `str` for finite value sets.
- Use `Field(default=...)` instead of positional defaults in Pydantic fields.

## Type placement

Keep type layers separate to avoid circular imports and make reviews easier.

- `tracecat/db/models.py`: SQLAlchemy tables only. Never add methods here.
- `schemas.py`: API request and response models.
- `types.py`: Domain dataclasses, protocols, aliases, and internal helper
  types.
- Prefer direct imports from submodules instead of re-exporting from
  `__init__.py`.
- Keep import direction shallow where possible:
  `models` -> `types` -> `schemas` -> `service` -> `router`.

## Services and context

- Services should inherit from `BaseService` when they need the standard logger,
  role fallback, and `with_session()` lifecycle handling.
- Request-scoped state lives in `tracecat/contexts.py`; use existing context
  variables instead of threading ad hoc state through unrelated layers.
- Keep router, service, schema, and type responsibilities separate:
  router for transport, service for business logic, schema for API contracts,
  and `types.py` for domain typing.
- Use predefined auth dependency types from `tracecat/auth/dependencies.py`
  instead of hand-rolling access checks in routes.

Route auth pattern:

```python
from tracecat.auth.dependencies import OrgAdminUser, WorkspaceUserRole

@router.get("/endpoint")
async def handler(role: WorkspaceUserRole) -> ResponseSchema:
    ...

@router.get("/admin")
async def admin_handler(role: OrgAdminUser) -> ResponseSchema:
    ...
```

Common role types:

- `WorkspaceUserRole`: user with workspace access.
- `ExecutorWorkspaceRole`: executor service with workspace access.
- `ServiceRole`: internal service role.
- `OrgAdminUser`: organization admin user.

## Database and SQLAlchemy

- Push filtering, aggregation, existence checks, sorting, and JSONB operations
  into SQLAlchemy/PostgreSQL instead of post-processing rows in Python.
- Prefer `select()` projections when only a few columns are needed.
- Use `RETURNING` when inserts, updates, or deletes need the resulting row.
- Use PostgreSQL upserts via `sqlalchemy.dialects.postgresql.insert`.
- Avoid N+1 query patterns; batch related reads with joins, `IN`, eager
  loading, or subqueries when that keeps the code clear.
- Use `.tuples().all()` when iterating over multi-column result sets.

## Pagination and API shape

- New list and search endpoints must use cursor-based pagination.
- Use `CursorPaginationParams` or the module-specific equivalent schema as the
  request contract.
- Keep collection routes on the canonical resource path instead of adding
  `/paginated` variants.
- Use one canonical paginated service method per resource rather than separate
  duplicated list and search implementations.
- Enforce pagination limits consistently in both request validation and route
  declarations.
- Cursor ordering must be deterministic and stable across pages.

## Testing

- `tests/unit/` should stay fast and isolated; mocks are acceptable there.
- `tests/integration/` should use live services and avoid mocks.
- `tests/backends/` is available for backend-specific test coverage.
- Prefer `@pytest.mark.anyio` for async tests.
- When tests need live third-party secrets, mark them with
  `@pytest.mark.live_secret` and keep them out of default CI runs.
- Bring up the cluster when work depends on PostgreSQL, Temporal, or other live
  services.

## Secrets and config

- Required secret accessors live in `tracecat/auth/secrets.py`. Call the helper
  at the point of use instead of reading raw config values directly.
- Do not default secrets to empty strings.
- In `tracecat/config.py`, prefer `int(os.environ.get("VAR") or default)` so
  empty environment variables do not break parsing.
- Prefer `orjson` over stdlib `json` when the dependency is available.

## Readability rules

- Optimize for the reviewer: focused diffs, explicit control flow, clear naming,
  and short single-purpose functions.
- Prefer guard clauses over deep nesting.
- Break dense expressions into named intermediate variables when that improves
  scanability.
- Use the walrus operator when it avoids redundant repeated calls in a
  conditional.
- Prefer `match`/`case` mapping patterns over long chains of `.get()` plus
  `isinstance` checks when extracting nested dictionary data.

## Registry and templates

- Templates live under
  `packages/tracecat-registry/tracecat_registry/templates/`.
- Keep template namespaces under `tools.`.
- Use `${{ }}` expressions with Tracecat operators such as `||` and `&&`, not
  Python boolean syntax.
- Use `core.http_request` with `payload` for JSON request bodies.
- Check `tracecat/expressions/functions.py` before using an `FN.` helper in a
  template. The helper must exist in `_FUNCTION_MAPPING`.
- Prefer `core.script.run_python` for complex template logic and optional
  parameter assembly.
- Registry SDK subclients already inherit a `/internal` prefix from
  `packages/tracecat-registry/tracecat_registry/sdk/client.py`; pass relative
  internal paths and add exact-path regression tests when you add new helpers.

Template rules and examples:

- Use `str | None` instead of `str | null` for optional types.
- Do not add `Content-Type` on GET requests.
- Use `${{ FN.url_encode(inputs.param) }}` when interpolating user input into
  URL paths.
- Return `${{ steps.step_name.result.data }}` directly unless a custom response
  shape is necessary.
- Let the platform handle standard HTTP errors unless a template needs
  additional edge-case handling.

```yaml
- ref: call_api
  action: core.http_request
  args:
    url: https://api.example.com/endpoint
    method: POST
    headers:
      Authorization: Bearer ${{ SECRETS.api.TOKEN }}
    payload:
      key: value
```

```yaml
value: ${{ inputs.login || inputs.email }}
```

```yaml
- ref: complex_logic
  action: core.script.run_python
  args:
    inputs:
      data: ${{ inputs.data }}
    script: |
      def main(data):
          return data
```

Common patterns:

```yaml
Authorization: Basic ${{ FN.to_base64(SECRETS.creds.USERNAME + ":" + SECRETS.creds.PASSWORD) }}
Authorization: Bearer ${{ SECRETS.api.TOKEN }}
value: ${{ inputs.override || defaults.standard_value }}
```
