from typing import Annotated, Any
from urllib.parse import urlsplit

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


def _split_s3_endpoint(endpoint_url: str) -> tuple[str, bool | None]:
    """Split a full endpoint URL into a DuckDB ``ENDPOINT`` host and ``USE_SSL``.

    The action accepts the same full-URL form as the other S3 actions (e.g.
    ``http://minio:9000``), but DuckDB's S3 secret wants a bare ``host[:port]``
    and does not infer TLS from the scheme. Strip the scheme for ``ENDPOINT``
    and derive ``USE_SSL`` from it: ``http`` -> False, ``https`` -> True, and
    no scheme -> None (leave DuckDB's default).
    """
    raw = endpoint_url.strip()
    # Prefix "//" for bare host:port so urlsplit populates netloc instead of path.
    parts = urlsplit(raw if "://" in raw else f"//{raw}")
    host = parts.netloc or raw
    use_ssl = (
        False if parts.scheme == "http" else True if parts.scheme == "https" else None
    )
    return host, use_ssl


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


def _maybe_setup_s3_secret(
    con: duckdb.DuckDBPyConnection, endpoint_url: str | None
) -> None:
    """Configure a DuckDB S3 secret from the ``amazon_s3`` credentials.

    No-op when no credentials are attached (the secret is optional). When
    present, credentials are resolved through the shared boto3 session so the
    full precedence applies, including cross-account ``AWS_ROLE_ARN``
    AssumeRole (the resolved temporary credentials carry a session token).
    """
    if not (
        secrets.get_or_default("AWS_ROLE_ARN")
        or secrets.get_or_default("AWS_ACCESS_KEY_ID")
    ):
        return

    session = aws_boto3.get_sync_session()
    credentials = session.get_credentials()
    if credentials is None:
        raise ValueError("Resolved AWS session has no credentials.")
    frozen = credentials.get_frozen_credentials()
    if not frozen.access_key or not frozen.secret_key:
        raise ValueError("Resolved AWS session is missing access key credentials.")

    # Only fixed option names and constants we derive ourselves are interpolated
    # into the statement; every credential value is bound via a ? parameter, so
    # secret values can never be parsed as SQL.
    options = ["TYPE s3", "KEY_ID ?", "SECRET ?"]
    params: list[Any] = [frozen.access_key, frozen.secret_key]
    if frozen.token:
        options.append("SESSION_TOKEN ?")
        params.append(frozen.token)
    if session.region_name:
        options.append("REGION ?")
        params.append(session.region_name)
    if endpoint_url:
        host, use_ssl = _split_s3_endpoint(endpoint_url)
        options.append("ENDPOINT ?")
        params.append(host)
        options.append("URL_STYLE 'path'")
        if use_ssl is not None:
            options.append(f"USE_SSL {'true' if use_ssl else 'false'}")

    con.execute("LOAD httpfs")
    con.execute(f"CREATE OR REPLACE SECRET __tc_s3 ({', '.join(options)})", params)


def _maybe_setup_http_secret(
    con: duckdb.DuckDBPyConnection, headers: dict[str, str] | None
) -> None:
    """Configure a DuckDB HTTP secret that injects ``headers`` into requests.

    The httpfs HTTP secret applies to both ``http://`` and ``https://`` reads.
    """
    if not headers:
        return

    # Bind the header names and values as two list parameters so they are never
    # interpolated into the statement text.
    con.execute("LOAD httpfs")
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
    s3_endpoint_url: Annotated[
        str | None,
        Doc(
            "Override the S3 endpoint URL (e.g. MinIO or another S3-compatible store)."
        ),
    ] = None,
) -> int | list[dict[str, Any]] | None:
    con = _connect()
    try:
        _maybe_setup_s3_secret(con, s3_endpoint_url)
        _maybe_setup_http_secret(con, headers)
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
