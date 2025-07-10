"""SQL database integration for PostgreSQL."""

import re
from typing import Annotated, Any, Literal

import asyncpg
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets

sql_secret = RegistrySecret(
    name="sql",
    keys=["SQL_USERNAME", "SQL_PASSWORD"],
)
"""SQL database credentials.

- name: `sql`
- keys:
    - `SQL_USERNAME`: Database username
    - `SQL_PASSWORD`: Database password
"""


@registry.register(
    default_title="Execute query",
    description="Execute a SQL query against a PostgreSQL database.",
    display_group="SQL",
    doc_url="https://magicstack.github.io/asyncpg/current/",
    namespace="tools.sql",
    secrets=[sql_secret],
)
async def execute_query(
    query: Annotated[
        str,
        Doc(
            "SQL query to execute. Use SELECT for read operations. Use $1, $2, etc. for parameters."
        ),
    ],
    host: Annotated[
        str,
        Doc("Database host address (e.g., 'localhost', '192.168.1.100')."),
    ],
    database_name: Annotated[
        str,
        Doc("Name of the database to connect to."),
    ],
    parameters: Annotated[
        list[Any] | None,
        Doc("List of parameters to bind to the query placeholders ($1, $2, etc.)."),
    ] = None,
    database_type: Annotated[
        Literal["postgresql"],
        Doc("Database type. Currently only PostgreSQL is supported."),
    ] = "postgresql",
    port: Annotated[
        int,
        Doc("Database port number."),
    ] = 5432,
    limit: Annotated[
        int | None,
        Doc("Maximum number of rows to return. If None, returns all rows."),
    ] = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Execute a SQL query and return results."""
    # asyncpg is always available if this module loads; no guard needed

    if database_type != "postgresql":
        raise ValueError(f"Unsupported database type: {database_type}")

    # Validate query for potential injection patterns
    _validate_query_safety(query)

    connection_string = _build_connection_string(host, port, database_name)

    # Apply limit to query if specified
    if limit is not None:
        query = _apply_limit_to_query(query, limit)

    # Prepare parameters
    params = parameters or []

    return await _execute_postgresql_query(connection_string, query, params)


@registry.register(
    default_title="Execute non-query",
    description="Execute a non-query SQL statement (INSERT, UPDATE, DELETE) against a PostgreSQL database.",
    display_group="SQL",
    doc_url="https://magicstack.github.io/asyncpg/current/",
    namespace="tools.sql",
    secrets=[sql_secret],
)
async def execute_non_query(
    statement: Annotated[
        str,
        Doc(
            "SQL statement to execute (INSERT, UPDATE, DELETE, CREATE, etc.). Use $1, $2, etc. for parameters."
        ),
    ],
    host: Annotated[
        str,
        Doc("Database host address (e.g., 'localhost', '192.168.1.100')."),
    ],
    database_name: Annotated[
        str,
        Doc("Name of the database to connect to."),
    ],
    parameters: Annotated[
        list[Any] | None,
        Doc("List of parameters to bind to the statement placeholders ($1, $2, etc.)."),
    ] = None,
    database_type: Annotated[
        Literal["postgresql"],
        Doc("Database type. Currently only PostgreSQL is supported."),
    ] = "postgresql",
    port: Annotated[
        int,
        Doc("Database port number."),
    ] = 5432,
) -> dict[str, Any]:
    """Execute a non-query SQL statement and return affected rows count."""
    # asyncpg is always available if this module loads; no guard needed

    if database_type != "postgresql":
        raise ValueError(f"Unsupported database type: {database_type}")

    # Validate statement for potential injection patterns
    _validate_query_safety(statement)

    connection_string = _build_connection_string(host, port, database_name)

    # Prepare parameters
    params = parameters or []

    return await _execute_postgresql_non_query(connection_string, statement, params)


@registry.register(
    default_title="Execute transaction",
    description="Execute multiple SQL statements in a transaction against a PostgreSQL database.",
    display_group="SQL",
    doc_url="https://magicstack.github.io/asyncpg/current/",
    namespace="tools.sql",
    secrets=[sql_secret],
)
async def execute_transaction(
    statements: Annotated[
        list[str],
        Doc(
            "List of SQL statements to execute in a transaction. Use $1, $2, etc. for parameters."
        ),
    ],
    host: Annotated[
        str,
        Doc("Database host address (e.g., 'localhost', '192.168.1.100')."),
    ],
    database_name: Annotated[
        str,
        Doc("Name of the database to connect to."),
    ],
    parameters: Annotated[
        list[list[Any]] | None,
        Doc(
            "List of parameter lists for each statement. Each inner list contains parameters for the corresponding statement."
        ),
    ] = None,
    database_type: Annotated[
        Literal["postgresql"],
        Doc("Database type. Currently only PostgreSQL is supported."),
    ] = "postgresql",
    port: Annotated[
        int,
        Doc("Database port number."),
    ] = 5432,
) -> dict[str, Any]:
    """Execute multiple SQL statements in a transaction."""
    # asyncpg is always available if this module loads; no guard needed

    if database_type != "postgresql":
        raise ValueError(f"Unsupported database type: {database_type}")

    # Validate all statements for potential injection patterns
    for statement in statements:
        _validate_query_safety(statement)

    connection_string = _build_connection_string(host, port, database_name)

    # Prepare parameters - ensure we have the right number of parameter lists
    params_list = parameters or []
    if len(params_list) != len(statements) and params_list:
        raise ValueError(
            f"Number of parameter lists ({len(params_list)}) must match number of statements ({len(statements)})"
        )

    # Pad with empty lists if no parameters provided
    if not params_list:
        params_list = [[] for _ in statements]

    conn = await asyncpg.connect(connection_string)
    try:
        async with conn.transaction():
            results = []
            for i, statement in enumerate(statements):
                stmt_params = params_list[i] if i < len(params_list) else []
                result = await conn.execute(statement, *stmt_params)
                # Extract affected rows from result string
                affected_rows = 0
                if result:
                    parts = result.split()
                    if len(parts) >= 2:
                        try:
                            affected_rows = int(parts[-1])
                        except ValueError:
                            pass
                results.append({"statement": statement, "affected_rows": affected_rows})

            return {"results": results, "status": "success"}
    finally:
        await conn.close()


def _validate_query_safety(query: str) -> None:
    """Validate query for potential injection patterns."""
    # Check for template expressions like ${{ something }}
    template_pattern = r"\$\{\{[^}]*\}\}"
    if re.search(template_pattern, query):
        raise ValueError(
            "Query contains template expressions (${{ ... }}) which could be vulnerable to injection. "
            "Use parameterized queries with $1, $2, etc. placeholders instead."
        )


def _build_connection_string(host: str, port: int, database_name: str) -> str:
    """Build PostgreSQL connection string from components."""
    username = secrets.get("SQL_USERNAME")
    password = secrets.get("SQL_PASSWORD")

    return f"postgresql://{username}:{password}@{host}:{port}/{database_name}"


def _apply_limit_to_query(query: str, limit: int) -> str:
    """Apply or replace LIMIT clause in a SQL query."""
    # Remove any trailing semicolon and whitespace
    query = query.rstrip("; \t\n\r")

    # Check if query already has a LIMIT clause (case-insensitive)
    # This regex matches LIMIT followed by a number, optionally with whitespace
    limit_pattern = r"\s+LIMIT\s+\d+\s*$"

    if re.search(limit_pattern, query, re.IGNORECASE):
        # Replace existing LIMIT clause
        query = re.sub(limit_pattern, f" LIMIT {limit}", query, flags=re.IGNORECASE)
    else:
        # Add new LIMIT clause
        query += f" LIMIT {limit}"

    return query


async def _execute_postgresql_query(
    connection_string: str,
    query: str,
    parameters: list[Any],
) -> dict[str, Any] | list[dict[str, Any]]:
    """Execute PostgreSQL query using asyncpg with parameterized queries."""
    conn = await asyncpg.connect(connection_string)
    try:
        rows = await conn.fetch(query, *parameters)

        # Convert rows to list of dictionaries
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def _execute_postgresql_non_query(
    connection_string: str,
    statement: str,
    parameters: list[Any],
) -> dict[str, Any]:
    """Execute PostgreSQL non-query statement using asyncpg with parameterized queries."""
    conn = await asyncpg.connect(connection_string)
    try:
        result = await conn.execute(statement, *parameters)
        # Extract affected rows from result string like "INSERT 0 1" or "UPDATE 2"
        affected_rows = 0
        if result:
            parts = result.split()
            if len(parts) >= 2:
                try:
                    affected_rows = int(parts[-1])
                except ValueError:
                    pass

        return {"affected_rows": affected_rows, "status": "success"}
    finally:
        await conn.close()
