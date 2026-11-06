"""Tests for the Postgres role bootstrap (``python -m tracecat.db.roles``).

Invariants:
- Bootstrap run by a non-superuser CREATEROLE admin (the RDS master's shape)
  creates the keyrings and login roles and is safe to rerun.
- Migrations run to head as ``migrator`` leave every ``public`` object owned
  by the owner keyring and none by ``migrator``.
- ``app`` is not a superuser, has no BYPASSRLS and owns nothing in
  ``public``, yet has DML there and can create, own (through the dynamic
  owner keyring) and alter a dynamic table with an FK to ``case``.
- The enforce-mode login check accepts ``app`` and refuses a superuser and
  ``migrator``.
- ``--sql`` output contains no passwords and applies the same topology.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.db.engine import verify_rls_enforceable_login
from tracecat.db.roles import (
    LoginMethod,
    LoginPasswords,
    RoleNames,
    UnsafeDatabaseLoginError,
    _admin_conninfo,
    assert_login_can_enforce_rls,
    bootstrap_statements,
    quote_ident,
    render_sql,
    run_bootstrap,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Pa%ss'word"  # % and ' check quoting end to end


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for these tests."""
    yield


def _url(user: str, password: str, database: str, driver: str = "") -> str:
    base = make_url(TEST_DB_CONFIG.sys_url_sync)
    url = base.set(
        drivername=f"postgresql{driver}",
        username=user,
        password=password,
        database=database,
    )
    return url.render_as_string(hide_password=False)


def _suffix() -> str:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master").replace("-", "_")
    return f"{worker}_{uuid.uuid4().hex[:8]}"


def _test_roles(suffix: str) -> RoleNames:
    return RoleNames(
        owner=f"owner_{suffix}",
        dynamic_owner=f"dyn_owner_{suffix}",
        readonly=f"readonly_{suffix}",
        migrator=f"migrator_{suffix}",
        app=f"app_{suffix}",
        agent_runtime=f"runtime_{suffix}",
    )


@dataclass(frozen=True, slots=True)
class Topology:
    database: str
    admin: str
    roles: RoleNames

    def url(self, user: str, driver: str = "") -> str:
        password = "admin" if user == self.admin else PASSWORD
        return _url(user, password, self.database, driver)


def _run(conn: psycopg.Connection, statement: str) -> psycopg.Cursor:
    """Execute SQL built from quoted test identifiers (psycopg wants LiteralString)."""
    return conn.execute(statement.encode())


def _superuser() -> psycopg.Connection:
    return psycopg.connect(_url("postgres", "postgres", "postgres"), autocommit=True)


def _drop_all(database: str, roles: list[str]) -> None:
    with _superuser() as conn:
        _run(conn, f"DROP DATABASE IF EXISTS {quote_ident(database)} WITH (FORCE)")
        for role in roles:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(role)}")


@pytest.fixture(scope="module")
def topology() -> Iterator[Topology]:
    """Bootstrap as an RDS-shaped admin, migrate as migrator, bootstrap again."""
    suffix = _suffix()
    topo = Topology(f"roles_{suffix}", f"admin_{suffix}", _test_roles(suffix))
    roles = topo.roles
    all_roles = [*roles.logins, *roles.keyrings, topo.admin]
    with _superuser() as conn:
        _run(
            conn,
            f"CREATE ROLE {quote_ident(topo.admin)} LOGIN CREATEROLE PASSWORD 'admin'",
        )
        _run(
            conn,
            f"CREATE DATABASE {quote_ident(topo.database)} OWNER {quote_ident(topo.admin)}",
        )
    passwords = LoginPasswords(PASSWORD, PASSWORD, PASSWORD)
    try:
        # Extensions need a superuser here (rds_superuser on RDS), as an admin would.
        with psycopg.connect(
            _url("postgres", "postgres", topo.database), autocommit=True
        ) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
        with psycopg.connect(topo.url(topo.admin), autocommit=True) as conn:
            notes = run_bootstrap(conn, LoginMethod.PASSWORD, passwords, roles)
        assert notes == [f"{name}: password set" for name in roles.logins]

        env = os.environ.copy()
        env["TRACECAT__DB_URI"] = topo.url(roles.migrator, "+psycopg")
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr[-4000:]

        # Rerun: idempotent, and now grants on the migrated objects too.
        with psycopg.connect(topo.url(topo.admin), autocommit=True) as conn:
            run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
        yield topo
    finally:
        _drop_all(topo.database, all_roles)


def test_keyrings_cannot_log_in_and_no_role_bypasses_rls(topology: Topology) -> None:
    roles = topology.roles
    with psycopg.connect(topology.url(topology.admin)) as conn:
        rows = conn.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = ANY(%s)",
            ([*roles.keyrings, *roles.logins],),
        ).fetchall()
    by_name = {name: (login, sup, bypass) for name, login, sup, bypass in rows}
    for keyring in roles.keyrings:
        assert by_name[keyring] == (False, False, False)
    for login in roles.logins:
        assert by_name[login] == (True, False, False)


def test_migrations_as_migrator_leave_public_owned_by_owner_keyring(
    topology: Topology,
) -> None:
    roles = topology.roles
    with psycopg.connect(topology.url(roles.migrator)) as conn:
        assert conn.execute("SELECT current_user").fetchone() == (roles.owner,)
        owners = dict(
            conn.execute(
                """
                SELECT pg_get_userbyid(c.relowner), count(*)
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_depend d ON d.objid = c.oid AND d.deptype = 'e'
                WHERE n.nspname = 'public' AND d.objid IS NULL
                GROUP BY 1
                """
            ).fetchall()
        )
    assert set(owners) == {roles.owner}
    assert owners[roles.owner] > 100


def test_app_has_dml_but_no_ownership_in_public(topology: Topology) -> None:
    roles = topology.roles
    with psycopg.connect(topology.url(roles.app)) as conn:
        missing = conn.execute(
            """
            SELECT c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND NOT has_table_privilege(c.oid, 'SELECT, INSERT, UPDATE, DELETE')
            """
        ).fetchall()
        assert missing == []
        assert conn.execute(
            "SELECT has_schema_privilege('public', 'CREATE')"
        ).fetchone() == (False,)
        assert conn.execute("SELECT count(*) FROM workflow").fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE public.not_allowed (id int)")


def test_readonly_keyring_can_only_select(topology: Topology) -> None:
    roles = topology.roles
    with psycopg.connect(topology.url(topology.admin)) as conn:
        row = conn.execute(
            "SELECT has_table_privilege(%s, 'public.workflow', 'SELECT'), "
            "has_table_privilege(%s, 'public.workflow', 'INSERT')",
            (roles.readonly, roles.readonly),
        ).fetchone()
    assert row == (True, False)


def test_app_creates_and_alters_dynamic_table_owned_by_keyring(
    topology: Topology,
) -> None:
    """Mirrors the case-fields path: CREATE SCHEMA/TABLE with an FK, re-own, alter."""
    roles = topology.roles
    schema = quote_ident(f"custom_fields_ws_{uuid.uuid4().hex[:12]}")
    dyn = quote_ident(roles.dynamic_owner)
    with psycopg.connect(topology.url(roles.app)) as conn:
        _run(conn, f"CREATE SCHEMA {schema}")
        _run(conn, f"ALTER SCHEMA {schema} OWNER TO {dyn}")
        _run(
            conn,
            f"CREATE TABLE {schema}.case_fields (id uuid PRIMARY KEY, "
            'case_id uuid NOT NULL REFERENCES public."case"(id) ON DELETE CASCADE)',
        )
        _run(conn, f"ALTER TABLE {schema}.case_fields OWNER TO {dyn}")
        _run(conn, f"ALTER TABLE {schema}.case_fields ENABLE ROW LEVEL SECURITY")
        _run(conn, f"ALTER TABLE {schema}.case_fields FORCE ROW LEVEL SECURITY")
        _run(conn, f"ALTER TABLE {schema}.case_fields ADD COLUMN note text")
        owner = _run(
            conn,
            f"SELECT pg_get_userbyid(relowner) FROM pg_class "
            f"WHERE oid = '{schema}.case_fields'::regclass",
        ).fetchone()
        conn.commit()
    assert owner == (roles.dynamic_owner,)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("user", "safe"),
    [("app", True), ("migrator", False), ("postgres", False)],
)
async def test_login_check_accepts_only_app(
    topology: Topology, user: str, safe: bool
) -> None:
    roles = topology.roles
    login = {"app": roles.app, "migrator": roles.migrator}.get(user, user)
    password = "postgres" if user == "postgres" else PASSWORD
    engine = create_async_engine(_url(login, password, topology.database, "+asyncpg"))
    try:
        async with engine.connect() as conn:
            if safe:
                await assert_login_can_enforce_rls(conn)
            else:
                with pytest.raises(UnsafeDatabaseLoginError):
                    await assert_login_can_enforce_rls(conn)
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_login_check_refuses_a_bypassrls_role_the_login_can_set(
    topology: Topology,
) -> None:
    """``SET ROLE`` to any role the login is a member of is the same as being it."""
    roles = topology.roles
    escape = f"escape_{_suffix()}"
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(escape)} NOLOGIN BYPASSRLS")
        _run(conn, f"GRANT {quote_ident(escape)} TO {quote_ident(roles.app)}")
    engine = create_async_engine(
        _url(roles.app, PASSWORD, topology.database, "+asyncpg")
    )
    try:
        async with engine.connect() as conn:
            with pytest.raises(UnsafeDatabaseLoginError, match="BYPASSRLS"):
                await assert_login_can_enforce_rls(conn)
    finally:
        await engine.dispose()
        with _superuser() as conn:
            _run(conn, f"DROP ROLE {quote_ident(escape)}")


@pytest.mark.anyio
async def test_startup_check_only_runs_in_enforce_mode(
    topology: Topology, monkeypatch: pytest.MonkeyPatch
) -> None:
    superuser_uri = _url("postgres", "postgres", topology.database, "+psycopg")
    monkeypatch.setattr(config, "TRACECAT__DB_URI", superuser_uri)
    monkeypatch.setattr(config, "TRACECAT__DB_PASS__ARN", None)
    monkeypatch.setattr(config, "TRACECAT__DB_USER", None)
    monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.SHADOW)
    await verify_rls_enforceable_login()
    monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.ENFORCE)
    with pytest.raises(UnsafeDatabaseLoginError, match="superuser"):
        await verify_rls_enforceable_login()


def test_sql_output_applies_topology_without_passwords() -> None:
    suffix = _suffix()
    database = f"roles_sql_{suffix}"
    roles = _test_roles(suffix)
    script = render_sql(LoginMethod.PASSWORD, roles)
    assert PASSWORD not in script
    assert "PASSWORD '" not in script
    with _superuser() as conn:
        _run(conn, f"CREATE DATABASE {quote_ident(database)}")
    try:
        with psycopg.connect(
            _url("postgres", "postgres", database), autocommit=True
        ) as conn:
            conn.execute(script.encode())
            members = conn.execute(
                "SELECT pg_has_role(%s, %s, 'MEMBER'), pg_has_role(%s, %s, 'MEMBER'), "
                "pg_has_role(%s, %s, 'MEMBER')",
                (
                    roles.app,
                    roles.dynamic_owner,
                    roles.migrator,
                    roles.owner,
                    roles.app,
                    roles.owner,
                ),
            ).fetchone()
        assert members == (True, True, False)
    finally:
        _drop_all(database, [*roles.logins, *roles.keyrings])


def test_token_login_methods_never_take_passwords() -> None:
    with pytest.raises(ValueError, match="only used with --login password"):
        run_bootstrap(
            psycopg.Connection.__new__(psycopg.Connection),
            LoginMethod.AWS_IAM,
            LoginPasswords(app="x"),
        )
    statements = "\n".join(bootstrap_statements(LoginMethod.AWS_IAM))
    assert 'GRANT "rds_iam" TO "migrator", "app", "agent_runtime";' in statements
    entra = "\n".join(bootstrap_statements(LoginMethod.AZURE_ENTRA))
    assert 'CREATE ROLE "app"' not in entra
    assert "pgaadauth_create_principal_with_oid" in entra


@dataclass(frozen=True, slots=True)
class Scratch:
    database: str
    admin: str
    roles: RoleNames

    def admin_conn(self) -> psycopg.Connection:
        return psycopg.connect(
            _url(self.admin, "admin", self.database), autocommit=True
        )


@pytest.fixture
def scratch() -> Iterator[Scratch]:
    """An empty database owned by an RDS-shaped admin, plus a fake IAM role."""
    suffix = _suffix()
    roles = _with_iam(suffix)
    scratch = Scratch(f"roles_scratch_{suffix}", f"admin_{suffix}", roles)
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.iam)} NOLOGIN")
        _run(
            conn,
            f"CREATE ROLE {quote_ident(scratch.admin)} LOGIN CREATEROLE PASSWORD 'admin'",
        )
        _run(
            conn,
            f"CREATE DATABASE {quote_ident(scratch.database)} OWNER {quote_ident(scratch.admin)}",
        )
    try:
        yield scratch
    finally:
        _drop_all(
            scratch.database, [*roles.logins, *roles.keyrings, scratch.admin, roles.iam]
        )


def _with_iam(suffix: str) -> RoleNames:
    base = _test_roles(suffix)
    return RoleNames(
        owner=base.owner,
        dynamic_owner=base.dynamic_owner,
        readonly=base.readonly,
        migrator=base.migrator,
        app=base.app,
        agent_runtime=base.agent_runtime,
        iam=f"iam_{suffix}",
    )


def _role_exists(name: str) -> bool:
    with _superuser() as conn:
        row = conn.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname = %s", (name,)
        ).fetchone()
    return row == (1,)


def test_admin_in_iam_chain_fails_closed(scratch: Scratch) -> None:
    """IAM bootstrap where the admin would end up in the IAM role changes nothing.

    PG 16 makes a CREATEROLE admin an ADMIN-only member of each login role it
    creates, so granting the IAM role to those logins puts the admin in it.
    """
    roles = scratch.roles
    with _superuser() as conn:
        # The admin needs ADMIN on the IAM role to grant it, as rds_superuser has.
        _run(
            conn,
            f"GRANT {quote_ident(roles.iam)} TO {quote_ident(scratch.admin)} "
            "WITH ADMIN TRUE, INHERIT FALSE, SET FALSE",
        )
    with (
        scratch.admin_conn() as conn,
        pytest.raises(psycopg.errors.RaiseException, match="password login"),
    ):
        run_bootstrap(conn, LoginMethod.AWS_IAM, LoginPasswords(), roles)
    assert not any(_role_exists(r) for r in (*roles.keyrings, *roles.logins))


def test_password_mode_refuses_login_role_in_iam(scratch: Scratch) -> None:
    """A password login role left in the IAM role would never accept its password."""
    roles = scratch.roles
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.app)} LOGIN")
        _run(conn, f"GRANT {quote_ident(roles.iam)} TO {quote_ident(roles.app)}")
    # A superuser admin is exempt from the admin check, so only this rule fires.
    superuser_url = _url("postgres", "postgres", scratch.database)
    with (
        psycopg.connect(superuser_url, autocommit=True) as conn,
        pytest.raises(psycopg.errors.RaiseException, match="password login"),
    ):
        run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(app=PASSWORD), roles)
    assert not _role_exists(roles.owner)


def test_refuses_roles_already_granted_on_another_database(scratch: Scratch) -> None:
    roles = scratch.roles
    other = f"{scratch.database}_other"
    with _superuser() as conn:
        _run(
            conn,
            f"CREATE DATABASE {quote_ident(other)} OWNER {quote_ident(scratch.admin)}",
        )
    try:
        with scratch.admin_conn() as conn:
            run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
        with psycopg.connect(
            _url(scratch.admin, "admin", other), autocommit=True
        ) as conn:
            with pytest.raises(psycopg.errors.RaiseException, match="another database"):
                run_bootstrap(
                    conn, LoginMethod.PASSWORD, LoginPasswords(app="other"), roles
                )
    finally:
        with _superuser() as conn:
            _run(conn, f"DROP DATABASE IF EXISTS {quote_ident(other)} WITH (FORCE)")


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("postgresql+psycopg://a:b@h/db", "sslmode=require"),
        ("postgresql://a:b@h/db?sslmode=verify-full", "sslmode=verify-full"),
    ],
)
def test_admin_uri_never_falls_back_to_plaintext(
    monkeypatch: pytest.MonkeyPatch, uri: str, expected: str
) -> None:
    monkeypatch.setattr(config, "TRACECAT__DB_ADMIN_URI", uri)
    monkeypatch.setattr(config, "TRACECAT__DB_SSLMODE", "require")
    conninfo = _admin_conninfo()
    assert conninfo.startswith("postgresql://a:b@h/db")
    assert conninfo.endswith(expected)
