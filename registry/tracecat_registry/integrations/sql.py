"""SQL database integration for PostgreSQL."""

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
        Doc("SQL query to execute. Use SELECT for read operations."),
    ],
    host: Annotated[
        str,
        Doc("Database host address (e.g., 'localhost', '192.168.1.100')."),
    ],
    database_name: Annotated[
        str,
        Doc("Name of the database to connect to."),
    ],
    database_type: Annotated[
        Literal["postgresql"],
        Doc("Database type. Currently only PostgreSQL is supported."),
    ] = "postgresql",
    port: Annotated[
        int,
        Doc("Database port number."),
    ] = 5432,
    fetch_mode: Annotated[
        Literal["all", "one", "many"],
        Doc(
            "Fetch mode: 'all' for all rows, 'one' for single row, 'many' for multiple rows."
        ),
    ] = "all",
    fetch_size: Annotated[
        int | None,
        Doc("Number of rows to fetch when using 'many' mode."),
    ] = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Execute a SQL query and return results."""
    if asyncpg is None:
        raise ImportError(
            "asyncpg is required for SQL integration. Install with: pip install asyncpg"
        )

    if database_type != "postgresql":
        raise ValueError(f"Unsupported database type: {database_type}")

    connection_string = _build_connection_string(host, port, database_name)

    return await _execute_postgresql_query(
        connection_string, query, fetch_mode, fetch_size
    )


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
        Doc("SQL statement to execute (INSERT, UPDATE, DELETE, CREATE, etc.)."),
    ],
    host: Annotated[
        str,
        Doc("Database host address (e.g., 'localhost', '192.168.1.100')."),
    ],
    database_name: Annotated[
        str,
        Doc("Name of the database to connect to."),
    ],
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
    if asyncpg is None:
        raise ImportError(
            "asyncpg is required for SQL integration. Install with: pip install asyncpg"
        )

    if database_type != "postgresql":
        raise ValueError(f"Unsupported database type: {database_type}")

    connection_string = _build_connection_string(host, port, database_name)

    return await _execute_postgresql_non_query(connection_string, statement)


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
        Doc("List of SQL statements to execute in a transaction."),
    ],
    host: Annotated[
        str,
        Doc("Database host address (e.g., 'localhost', '192.168.1.100')."),
    ],
    database_name: Annotated[
        str,
        Doc("Name of the database to connect to."),
    ],
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
    if asyncpg is None:
        raise ImportError(
            "asyncpg is required for SQL integration. Install with: pip install asyncpg"
        )

    if database_type != "postgresql":
        raise ValueError(f"Unsupported database type: {database_type}")

    connection_string = _build_connection_string(host, port, database_name)

    conn = await asyncpg.connect(connection_string)
    try:
        async with conn.transaction():
            results = []
            for statement in statements:
                result = await conn.execute(statement)
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


def _build_connection_string(host: str, port: int, database_name: str) -> str:
    """Build PostgreSQL connection string from components."""
    username = secrets.get("SQL_USERNAME")
    password = secrets.get("SQL_PASSWORD")

    return f"postgresql://{username}:{password}@{host}:{port}/{database_name}"


async def _execute_postgresql_query(
    connection_string: str,
    query: str,
    fetch_mode: str,
    fetch_size: int | None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Execute PostgreSQL query using asyncpg."""
    if asyncpg is None:
        raise ImportError("asyncpg is required for SQL integration")

    conn = await asyncpg.connect(connection_string)
    try:
        if fetch_mode == "all":
            rows = await conn.fetch(query)
        elif fetch_mode == "one":
            row = await conn.fetchrow(query)
            if row:
                return dict(row)
            return {}
        elif fetch_mode == "many":
            if fetch_size is None:
                raise ValueError("fetch_size is required when using 'many' mode")
            rows = await conn.fetch(query, limit=fetch_size)
        else:
            raise ValueError(f"Invalid fetch_mode: {fetch_mode}")

        # Convert rows to list of dictionaries
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def _execute_postgresql_non_query(
    connection_string: str,
    statement: str,
) -> dict[str, Any]:
    """Execute PostgreSQL non-query statement using asyncpg."""
    if asyncpg is None:
        raise ImportError("asyncpg is required for SQL integration")

    conn = await asyncpg.connect(connection_string)
    try:
        result = await conn.execute(statement)
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
