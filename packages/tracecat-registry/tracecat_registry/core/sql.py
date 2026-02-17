"""Secure SQL actions via sqlalchemy using `core.sql.execute_query`."""

import hmac
from typing import Annotated, Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, config, registry, secrets


# Maximum number of rows to return from a query by default
DEFAULT_MAX_ROWS = config.TRACECAT__LIMIT_CURSOR_MAX

# Registry secret for SQL connections
sql_secret = RegistrySecret(
    name="sql",
    keys=["CONNECTION_URL"],
)
"""SQL connection secret.

Required keys:
- `CONNECTION_URL`: SQLAlchemy connection URL (e.g., 'postgresql+psycopg://user:pass@host:port/dbname')

Common driver formats:
- PostgreSQL: postgresql+psycopg://, postgresql+psycopg2://, postgresql+asyncpg://
- MySQL: mysql+pymysql://, mysql+mysqlclient://, mysql+mysql-connector-python://
- MSSQL: mssql+pyodbc://, mssql+pymssql://
- Oracle: oracle+cx_oracle://
- SQLite: sqlite+pysqlite://
"""


class SQLConnectionValidationError(Exception):
    """Raised when SQL connection validation fails."""


def _validate_connection_url(connection_url: URL) -> None:
    """Validate that the connection URL does not point to Tracecat's internal database.

    We only compare against the configured internal database endpoint/port and never
    surface credentials from the internal URI to the user.

    In sandboxed execution, network isolation is the primary security control.
    The internal DB config is intentionally not passed to the sandbox.

    Args:
        connection_url: SQLAlchemy URL object

    Raises:
        SQLConnectionValidationError: If connection attempts to access Tracecat's database
    """
    # Parse internal database URL
    try:
        internal_url = make_url(config.TRACECAT__DB_URI)
    except Exception as exc:  # pragma: no cover - defensive fail-closed path
        raise SQLConnectionValidationError(
            "Internal database configuration error. Cannot validate connection safety."
        ) from exc

    # Resolve the internal endpoint from explicit config first, else fallback to the URI
    internal_host = config.TRACECAT__DB_ENDPOINT or internal_url.host
    if not internal_host:
        raise SQLConnectionValidationError(
            "Internal database endpoint is not configured. Cannot validate connection safety."
        )

    try:
        internal_port = (
            int(config.TRACECAT__DB_PORT)
            if config.TRACECAT__DB_PORT is not None
            else None
        )
    except ValueError as exc:
        raise SQLConnectionValidationError(
            "Internal database port configuration is invalid. Cannot validate connection safety."
        ) from exc

    internal_port = internal_port or internal_url.port or 5432

    if connection_url.host:
        user_host = connection_url.host.lower()
        internal_host_lower = internal_host.lower()
        if hmac.compare_digest(user_host, internal_host_lower):
            user_port = connection_url.port or 5432
            if user_port == internal_port:
                raise SQLConnectionValidationError(
                    "Cannot connect to Tracecat's internal database endpoint. Use an external database connection URL instead."
                )


def _create_engine_with_validation(connection_url: URL) -> Engine:
    """Create a SQLAlchemy engine with security validation.

    Args:
        connection_url: SQLAlchemy URL object

    Returns:
        SQLAlchemy Engine

    Raises:
        SQLConnectionValidationError: If connection is unsafe
    """
    # Validate connection safety
    _validate_connection_url(connection_url)

    # Use NullPool to avoid double-pooling in Lambda-style executions and defer pooling
    # to upstream services (e.g., RDS Proxy/pgBouncer).
    engine = create_engine(
        connection_url,
        poolclass=NullPool,
        pool_pre_ping=True,
        hide_parameters=True,  # Hide parameters in logs for security
    )
    return engine


@registry.register(
    default_title="Execute SQL query",
    description="Execute a parameterized SQL query on an external database with security controls.",
    display_group="Database",
    namespace="core.sql",
    secrets=[sql_secret],
)
async def execute_query(
    query: Annotated[
        str,
        Doc(
            "SQL query to execute. Use :param_name syntax for bound parameters. "
            "Do NOT use Tracecat expressions in the query string.",
        ),
    ],
    bound_params: Annotated[
        dict[str, Any] | None,
        Doc(
            "Bound query parameters as a dictionary (injected with :param_name syntax). "
            "Supply dynamic values here, NOT within the query string. "
            "This is required for safe, parameterized SQL queries."
        ),
    ] = None,
    fetch_one: Annotated[
        bool,
        Doc(
            "Return a single row instead of a list of rows. Defaults to False, which fetches all rows."
        ),
    ] = False,
    max_rows: Annotated[
        int,
        Doc(
            f"Maximum number of rows to return. Default {DEFAULT_MAX_ROWS}. "
            "Prevents accidentally returning huge result sets."
        ),
    ] = DEFAULT_MAX_ROWS,
) -> int | dict[str, Any] | list[dict[str, Any]] | None:
    """Execute a parameterized SQL query on an external database."""
    # Get connection URL from secrets and parse it
    connection_url_str = secrets.get("CONNECTION_URL")
    try:
        connection_url = make_url(connection_url_str)
    except Exception as e:
        raise ValueError(f"Invalid CONNECTION_URL format: {e}") from e

    engine = _create_engine_with_validation(connection_url)

    try:
        stmt = text(query)
        parameters: dict[str, Any] = bound_params or {}

        with engine.begin() as conn:
            result = conn.execute(stmt, parameters)

            if result.returns_rows:
                mappings = result.mappings()
                if fetch_one:
                    row = mappings.fetchone()
                    return dict(row) if row else None
                else:
                    row_mappings = result.mappings().fetchmany(size=max_rows)
                    rows = [dict(row) for row in row_mappings]
                    return rows

            return result.rowcount
    except SQLAlchemyError:
        raise
    finally:
        engine.dispose()
