"""Provision the least-privileged PostgreSQL role used by the load collector.

The provisioning DSN must identify the same database role that creates Tracecat
workspace tables. It needs permission to create/grant the monitor role and to
grant access in the selected workspace schema, but this command does not require
or assume that the role is a PostgreSQL superuser.

Normally invoked by ``just cluster loadtest``. Direct diagnostic usage:

    export TRACECAT_LOADTEST_PROVISION_DSN=postgresql://.../postgres
    export TRACECAT_LOADTEST_MONITOR_DSN="$(
        uv run --all-packages python -m tracecat_benchmark.provision_monitor \\
            --workspace-id 00000000-0000-4000-8000-000000000000
    )"

The command prints only the generated monitor DSN so callers can capture it
without writing credentials into the repository or benchmark artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import secrets
import sys
from typing import Final
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import asyncpg

from .collector import _workspace_schema_name

DEFAULT_PROVISION_DSN_ENV: Final = "TRACECAT_LOADTEST_PROVISION_DSN"
DEFAULT_MONITOR_PASSWORD_ENV: Final = "TRACECAT_LOADTEST_MONITOR_PASSWORD"
DEFAULT_MONITOR_ROLE: Final = "scatter_load_monitor"
IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DSN_CREDENTIAL_QUERY_KEYS: Final = frozenset(
    {
        "access_token",
        "api_key",
        "pass",
        "passfile",
        "passwd",
        "password",
        "refresh_token",
        "secret",
        "sslpassword",
        "token",
        "user",
        "username",
    }
)
DSN_CREDENTIAL_QUERY_SUFFIXES: Final = (
    "_api_key",
    "_passwd",
    "_password",
    "_secret",
    "_token",
)


class MonitorProvisioningError(RuntimeError):
    """The requested monitor role could not be provisioned safely."""


async def _quoted_identifier(conn: asyncpg.Connection, value: str) -> str:
    quoted = await conn.fetchval("SELECT quote_ident($1)", value)
    if not isinstance(quoted, str):
        raise MonitorProvisioningError("PostgreSQL could not quote an identifier")
    return quoted


async def _quoted_literal(conn: asyncpg.Connection, value: str) -> str:
    quoted = await conn.fetchval("SELECT quote_literal($1)", value)
    if not isinstance(quoted, str):
        raise MonitorProvisioningError("PostgreSQL could not quote a literal")
    return quoted


def _monitor_dsn(provision_dsn: str, role: str, password: str) -> str:
    """Reuse only the local database endpoint, replacing its credentials."""
    parsed = urlsplit(provision_dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname is None:
        raise MonitorProvisioningError("provisioning DSN must be a PostgreSQL TCP URL")
    host = parsed.hostname
    rendered_host = f"[{host}]" if ":" in host else host
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{rendered_host}{port}"
    query = urlencode(
        [
            (name, value)
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
            if (normalized_name := name.casefold().replace("-", "_"))
            not in DSN_CREDENTIAL_QUERY_KEYS
            and not normalized_name.endswith(DSN_CREDENTIAL_QUERY_SUFFIXES)
        ]
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))


async def provision_monitor_role(
    provision_dsn: str,
    workspace_id: str,
    role: str,
    password: str,
) -> str:
    """Create the schema and monitor with current plus future table access."""
    if IDENTIFIER_RE.fullmatch(role) is None:
        raise MonitorProvisioningError("monitor role must be a SQL identifier")
    schema = _workspace_schema_name(workspace_id)
    conn = await asyncpg.connect(
        provision_dsn,
        server_settings={"application_name": "load-test-monitor-provisioner"},
    )
    try:
        database = await conn.fetchval("SELECT current_database()")
        if not isinstance(database, str):
            raise MonitorProvisioningError(
                "PostgreSQL did not report the current database"
            )
        role_identifier = await _quoted_identifier(conn, role)
        schema_identifier = await _quoted_identifier(conn, schema)
        database_identifier = await _quoted_identifier(conn, database)
        password_literal = await _quoted_literal(conn, password)
        existing = await conn.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname = $1",
            role,
        )
        if existing is not None:
            raise MonitorProvisioningError(
                "refusing to alter an existing PostgreSQL role; use a fresh "
                "benchmark cluster or a new monitor role"
            )

        async with conn.transaction():
            # TablesService normally creates this schema with the first table.
            # Provision it earlier so the collector can become ready before the
            # runner creates the synthetic fixture table.
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_identifier}")
            await conn.execute(
                f"CREATE ROLE {role_identifier} LOGIN PASSWORD {password_literal}"
            )
            await conn.execute(f"GRANT pg_read_all_stats TO {role_identifier}")
            await conn.execute(
                f"GRANT CONNECT ON DATABASE {database_identifier} TO {role_identifier}"
            )
            await conn.execute(
                f"GRANT USAGE ON SCHEMA {schema_identifier} TO {role_identifier}"
            )
            await conn.execute(
                f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema_identifier} "
                f"TO {role_identifier}"
            )
            # The provisioning connection must use the same database role that
            # creates workspace tables. Its default privileges then cover the
            # synthetic table that the runner creates after collector readiness.
            await conn.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema_identifier} "
                f"GRANT SELECT ON TABLES TO {role_identifier}"
            )
    finally:
        await conn.close()

    return _monitor_dsn(provision_dsn, role, password)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracecat_benchmark.provision_monitor",
        description="Provision the PostgreSQL monitoring role for one workspace.",
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--monitor-role", default=DEFAULT_MONITOR_ROLE)
    parser.add_argument(
        "--provision-dsn-env",
        default=DEFAULT_PROVISION_DSN_ENV,
        help=(
            "Environment variable containing a provisioning DSN for the same "
            "database role that creates Tracecat workspace tables."
        ),
    )
    parser.add_argument(
        "--password-env",
        default=DEFAULT_MONITOR_PASSWORD_ENV,
        help=(
            "Optional environment variable containing the synthetic monitor "
            "password. A random password is generated when unset."
        ),
    )
    return parser


async def amain(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    provision_dsn = os.environ.get(args.provision_dsn_env)
    if not provision_dsn:
        print(
            f"No provisioning DSN provided; set {args.provision_dsn_env}",
            file=sys.stderr,
        )
        return 2
    password = os.environ.get(args.password_env) or secrets.token_urlsafe(32)
    try:
        monitor_dsn = await provision_monitor_role(
            provision_dsn,
            args.workspace_id,
            args.monitor_role,
            password,
        )
    except (
        asyncpg.PostgresError,
        MonitorProvisioningError,
        OSError,
        ValueError,
    ) as exc:
        print(
            f"Monitor role provisioning failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2
    print(monitor_dsn)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
