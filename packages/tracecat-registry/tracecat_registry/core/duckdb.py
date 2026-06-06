import os
from typing import Annotated, Any

import duckdb
from pydantic_core import to_jsonable_python
from typing_extensions import Doc

import tracecat_registry.integrations.aws_boto3 as aws_boto3
from tracecat_registry import registry, secrets
from tracecat_registry.config import TRACECAT__DUCKDB_EXTENSION_DIRECTORY
from tracecat_registry.integrations.amazon_s3 import s3_secret

# Directory the Docker images preinstall DuckDB extensions into. Used as a
# fallback when TRACECAT__DUCKDB_EXTENSION_DIRECTORY is not set in the process
# environment — notably under nsjail executors, where the env var is not
# forwarded into the jail but this directory is bind-mounted (read-only) from
# the sandbox rootfs.
_DEFAULT_EXTENSION_DIRECTORY = "/usr/local/lib/duckdb/extensions"


def _rows_to_json(
    columns: list[str], rows: list[tuple[Any, ...]]
) -> list[dict[str, Any]]:
    records = [dict(zip(columns, row)) for row in rows]
    return to_jsonable_python(records, fallback=str, exclude_none=False)


def _extension_directory() -> str | None:
    """Directory DuckDB should load extensions from, or None for its default.

    Prefer the configured ``TRACECAT__DUCKDB_EXTENSION_DIRECTORY``; otherwise fall
    back to the image's preinstalled directory when it exists. The fallback makes
    the pinned extensions usable across every executor backend — including nsjail
    sandboxes, where the env var is not forwarded into the jail but the directory
    is bind-mounted read-only from the rootfs. Returns None in local/dev where
    neither is present, so DuckDB keeps its default autoinstall behaviour.
    """
    if TRACECAT__DUCKDB_EXTENSION_DIRECTORY:
        return TRACECAT__DUCKDB_EXTENSION_DIRECTORY
    if os.path.isdir(_DEFAULT_EXTENSION_DIRECTORY):
        return _DEFAULT_EXTENSION_DIRECTORY
    return None


def _connect() -> duckdb.DuckDBPyConnection:
    """Open an in-process DuckDB connection.

    Loads extensions from the preinstalled directory (see ``_extension_directory``)
    instead of autoinstalling over the network when that directory is available.
    """
    if extension_directory := _extension_directory():
        return duckdb.connect(config={"extension_directory": extension_directory})
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


def _setup_s3_secret(con: duckdb.DuckDBPyConnection) -> None:
    """Create the S3 secret used for ``s3://`` reads, when credentials are attached.

    S3 access goes through httpfs, so it is loaded only when the ``amazon_s3``
    secret carries credentials. Plain queries (no credentials) load nothing and
    create nothing.
    """
    s3 = _build_s3_secret()
    if s3 is None:
        return

    options, params = s3
    con.execute("LOAD httpfs")
    con.execute(f"CREATE OR REPLACE SECRET __tc_s3 ({', '.join(options)})", params)


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
) -> int | list[dict[str, Any]] | None:
    con = _connect()
    try:
        _setup_s3_secret(con)
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
