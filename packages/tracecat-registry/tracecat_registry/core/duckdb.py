from typing import Annotated, Any

import duckdb
from pydantic_core import to_jsonable_python
from typing_extensions import Doc

import tracecat_registry.integrations.aws_boto3 as aws_boto3
from tracecat_registry import registry, secrets
from tracecat_registry.config import TRACECAT__DUCKDB_EXTENSION_DIRECTORY
from tracecat_registry.integrations.amazon_s3 import s3_secret


def _rows_to_json(
    columns: list[str], rows: list[tuple[Any, ...]]
) -> list[dict[str, Any]]:
    records = [dict(zip(columns, row)) for row in rows]
    return to_jsonable_python(records, fallback=str, exclude_none=False)


def _connect() -> duckdb.DuckDBPyConnection:
    """Open an in-process DuckDB connection.

    When ``TRACECAT__DUCKDB_EXTENSION_DIRECTORY`` is set (the Docker images
    point it at the preinstalled extension directory) the connection loads
    extensions from there instead of autoinstalling over the network.
    """
    if TRACECAT__DUCKDB_EXTENSION_DIRECTORY:
        return duckdb.connect(
            config={"extension_directory": TRACECAT__DUCKDB_EXTENSION_DIRECTORY}
        )
    return duckdb.connect()


def _build_s3_secret() -> tuple[list[str], list[Any]] | None:
    """Build a parameterized S3 ``CREATE SECRET`` spec from the ``amazon_s3`` creds.

    Returns ``(options, params)`` for the secret, or ``None`` when no credentials
    are attached (the secret is optional). Credentials are resolved through the
    shared boto3 session so the full precedence applies, including cross-account
    ``AWS_ROLE_ARN`` AssumeRole (the temporary credentials carry a session token).

    Only fixed option names are placed in the statement text; every credential
    value is bound via a ``?`` parameter, so a secret value can never be parsed
    as SQL.
    """
    if not (
        secrets.get_or_default("AWS_ROLE_ARN")
        or secrets.get_or_default("AWS_ACCESS_KEY_ID")
    ):
        return None

    session = aws_boto3.get_sync_session()
    credentials = session.get_credentials()
    if credentials is None:
        raise ValueError("Resolved AWS session has no credentials.")
    frozen = credentials.get_frozen_credentials()
    if not frozen.access_key or not frozen.secret_key:
        raise ValueError("Resolved AWS session is missing access key credentials.")

    options = ["TYPE s3", "KEY_ID ?", "SECRET ?"]
    params: list[Any] = [frozen.access_key, frozen.secret_key]
    if frozen.token:
        options.append("SESSION_TOKEN ?")
        params.append(frozen.token)
    if session.region_name:
        options.append("REGION ?")
        params.append(session.region_name)
    return options, params


def _setup_remote_secrets(
    con: duckdb.DuckDBPyConnection, headers: dict[str, str] | None
) -> None:
    """Create the S3 and/or HTTP secrets used for remote reads.

    Both secret types require httpfs, so it is loaded once here, and only when a
    secret is actually needed. Plain queries (no ``amazon_s3`` credentials and no
    headers) load nothing and create nothing.
    """
    s3 = _build_s3_secret()
    if s3 is None and not headers:
        return

    con.execute("LOAD httpfs")

    if s3 is not None:
        options, params = s3
        con.execute(f"CREATE OR REPLACE SECRET __tc_s3 ({', '.join(options)})", params)

    if headers:
        # Bind header names and values as two list parameters (never interpolated).
        con.execute(
            "CREATE OR REPLACE SECRET __tc_http (TYPE http, EXTRA_HTTP_HEADERS map(?, ?))",
            [list(headers.keys()), list(headers.values())],
        )


@registry.register(
    default_title="Execute DuckDB SQL",
    description=("Execute SQL in an in-process DuckDB engine"),
    display_group="DuckDB",
    namespace="core.duckdb",
    secrets=[s3_secret],
)
def execute_sql(
    sql: Annotated[
        str,
        Doc("SQL to execute in an in-process DuckDB connection. "),
    ],
    headers: Annotated[
        dict[str, str] | None,
        Doc(
            "HTTP headers sent with httpfs http(s) requests, e.g. for "
            "authenticating reads from a remote URL."
        ),
    ] = None,
) -> int | list[dict[str, Any]] | None:
    con = _connect()
    try:
        _setup_remote_secrets(con, headers)
        con.execute(sql)
        if con.description is None:
            return con.rowcount
        columns = [column[0] for column in con.description]
        if not columns:
            return con.rowcount
        rows = con.fetchall()
        return _rows_to_json(columns, rows)
    finally:
        con.close()
