import os
from typing import Annotated, Any
from urllib.parse import urlsplit

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


def _normalize_headers_scope(headers_scope: str | None) -> str:
    """Validate and normalize the HTTP secret ``SCOPE`` for header injection.

    DuckDB matches ``SCOPE`` as a literal URL prefix, so a bare host prefix like
    ``https://api.example.com`` would also match ``https://api.example.com.evil.com``.
    Require an http(s) URL with a host, and anchor the host boundary with a
    trailing ``/`` when no path is given, so the headers can never leak to a
    sibling host. Query/fragment are dropped.
    """
    if not headers_scope:
        raise ValueError(
            "headers_scope is required when headers is set: provide the URL prefix "
            "the headers apply to (e.g. https://api.example.com) so they are not "
            "sent to other hosts."
        )
    parts = urlsplit(headers_scope)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(
            "headers_scope must be an http:// or https:// URL with a host, "
            f"e.g. https://api.example.com (got {headers_scope!r})."
        )
    # Anchor the host with a trailing slash so prefix matching cannot bleed into a
    # longer host (e.g. api.example.com.evil.com).
    path = parts.path or "/"
    return f"{parts.scheme}://{parts.netloc}{path}"


def _setup_remote_secrets(
    con: duckdb.DuckDBPyConnection,
    headers: dict[str, str] | None,
    headers_scope: str | None,
) -> None:
    """Create the S3 and/or HTTP secrets used for remote reads.

    Both secret types require httpfs, so it is loaded once here, and only when a
    secret is actually needed. Plain queries (no ``amazon_s3`` credentials and no
    headers) load nothing and create nothing.

    The HTTP secret is always scoped: ``headers_scope`` is required when
    ``headers`` is set, so the headers (e.g. an auth token) are only ever sent to
    requests whose URL starts with that normalized prefix — never to other hosts a
    query might also read.
    """
    scope = _normalize_headers_scope(headers_scope) if headers else None

    s3 = _build_s3_secret()
    if s3 is None and not headers:
        return

    con.execute("LOAD httpfs")

    if s3 is not None:
        options, params = s3
        con.execute(f"CREATE OR REPLACE SECRET __tc_s3 ({', '.join(options)})", params)

    if headers:
        # Bind header names, values, and scope as parameters (never interpolated).
        con.execute(
            "CREATE OR REPLACE SECRET __tc_http "
            "(TYPE http, EXTRA_HTTP_HEADERS map(?, ?), SCOPE ?)",
            [list(headers.keys()), list(headers.values()), scope],
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
            "authenticating reads from a remote URL. Requires headers_scope."
        ),
    ] = None,
    headers_scope: Annotated[
        str | None,
        Doc(
            "URL prefix the headers are restricted to (e.g. "
            "https://api.example.com). Required when headers is set; the headers "
            "are only sent to requests whose URL starts with this prefix, so auth "
            "tokens never leak to other hosts."
        ),
    ] = None,
) -> int | list[dict[str, Any]] | None:
    con = _connect()
    try:
        _setup_remote_secrets(con, headers, headers_scope)
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
