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
import re
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
from tracecat.db.models import Base
from tracecat.db.roles import (
    READONLY_HIDDEN_COLUMNS,
    LoginMethod,
    LoginPasswords,
    RoleNames,
    UnsafeDatabaseLoginError,
    UpgradeResult,
    _admin_conninfo,
    _printable,
    assert_login_can_enforce_rls,
    bootstrap_statements,
    migrate,
    quote_ident,
    quote_literal,
    render_sql,
    run_bootstrap,
    run_upgrade,
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


def test_readonly_keyring_never_reads_credentials(topology: Topology) -> None:
    """Bearer tokens and password hashes stay hidden; the rest of the row doesn't."""
    roles = topology.roles
    # The security contract, kept apart from the implementation on purpose:
    # dropping a column from READONLY_HIDDEN_COLUMNS must fail this test.
    credential_columns = {
        ("access_token", "token"),
        ("oauth_account", "access_token"),
        ("oauth_account", "refresh_token"),
        ("invitation", "token"),
        ("organization_invitation", "token"),
        ("organization_domain", "verification_token"),
        ("user", "hashed_password"),
        ("mfa_recovery_code", "code_hash"),
        ("agent_channel_token", "config"),
    }
    with psycopg.connect(topology.url(topology.admin)) as conn:
        for table, column in sorted(credential_columns):
            row = conn.execute(
                "SELECT has_column_privilege(%s, %s::regclass, %s, 'SELECT')",
                (roles.readonly, f'public."{table}"', column),
            ).fetchone()
            assert row == (False,), (table, column)
        visible = conn.execute(
            "SELECT has_column_privilege(%s, 'public.\"user\"'::regclass, 'email', 'SELECT'), "
            "has_column_privilege(%s, 'public.access_token'::regclass, 'user_id', 'SELECT')",
            (roles.readonly, roles.readonly),
        ).fetchone()
    assert visible == (True, True)


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
    [("app", True), ("migrator", False), ("postgres", False), ("admin", False)],
)
async def test_login_check_accepts_only_app(
    topology: Topology, user: str, safe: bool
) -> None:
    roles = topology.roles
    login = {"app": roles.app, "migrator": roles.migrator, "admin": topology.admin}.get(
        user, user
    )
    password = {"postgres": "postgres", "admin": "admin"}.get(user, PASSWORD)
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


def test_refuses_roles_with_object_grants_in_another_database(scratch: Scratch) -> None:
    """A legacy install that only granted tables (default PUBLIC CONNECT) counts too."""
    roles = scratch.roles
    other = f"{scratch.database}_objs"
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.app)} LOGIN")
        _run(conn, f"CREATE DATABASE {quote_ident(other)}")
    try:
        with psycopg.connect(
            _url("postgres", "postgres", other), autocommit=True
        ) as conn:
            conn.execute("CREATE TABLE legacy (id int)")
            _run(conn, f"GRANT SELECT ON legacy TO {quote_ident(roles.app)}")
        with (
            psycopg.connect(
                _url("postgres", "postgres", scratch.database), autocommit=True
            ) as conn,
            pytest.raises(psycopg.errors.RaiseException, match="another database"),
        ):
            run_bootstrap(
                conn, LoginMethod.PASSWORD, LoginPasswords(app=PASSWORD), roles
            )
    finally:
        with _superuser() as conn:
            _run(conn, f"DROP DATABASE IF EXISTS {quote_ident(other)} WITH (FORCE)")


def test_refuses_roles_inheriting_grants_in_another_database(scratch: Scratch) -> None:
    """Access through a legacy group role in another database counts too."""
    roles = scratch.roles
    other = f"{scratch.database}_inh"
    group = f"legacy_reader_{_suffix()}"
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.app)} LOGIN")
        _run(conn, f"CREATE ROLE {quote_ident(group)} NOLOGIN")
        _run(conn, f"GRANT {quote_ident(group)} TO {quote_ident(roles.app)}")
        _run(conn, f"CREATE DATABASE {quote_ident(other)}")
    try:
        with psycopg.connect(
            _url("postgres", "postgres", other), autocommit=True
        ) as conn:
            conn.execute("CREATE TABLE legacy (id int)")
            _run(conn, f"GRANT SELECT ON legacy TO {quote_ident(group)}")
        with (
            psycopg.connect(
                _url("postgres", "postgres", scratch.database), autocommit=True
            ) as conn,
            pytest.raises(psycopg.errors.RaiseException, match="another database"),
        ):
            run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
    finally:
        with _superuser() as conn:
            _run(conn, f"DROP DATABASE IF EXISTS {quote_ident(other)} WITH (FORCE)")
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(group)}")


def test_bootstrap_normalizes_noinherit_roles(scratch: Scratch) -> None:
    """Before PG 16, a NOINHERIT member gets no owner rights from the keyring."""
    roles = scratch.roles
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.app)} LOGIN NOINHERIT")
    with psycopg.connect(
        _url("postgres", "postgres", scratch.database), autocommit=True
    ) as conn:
        run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
        rows = conn.execute(
            "SELECT bool_and(rolinherit) FROM pg_roles WHERE rolname = ANY(%s)",
            ([*roles.keyrings, *roles.logins],),
        ).fetchone()
    assert rows == (True,)


def test_refuses_a_login_role_with_unexpected_members(scratch: Scratch) -> None:
    """A pre-created migrator with a member would hand that member the owner keyring."""
    roles = scratch.roles
    squatter = f"squatter_{_suffix()}"
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.migrator)} LOGIN")
        _run(conn, f"CREATE ROLE {quote_ident(squatter)} LOGIN")
        _run(conn, f"GRANT {quote_ident(roles.migrator)} TO {quote_ident(squatter)}")
    try:
        with (
            psycopg.connect(
                _url("postgres", "postgres", scratch.database), autocommit=True
            ) as conn,
            pytest.raises(psycopg.errors.RaiseException, match="unexpected members"),
        ):
            run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
        assert not _role_exists(roles.owner)
    finally:
        with _superuser() as conn:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(squatter)}")


def test_refuses_admin_only_members_other_than_the_admin(scratch: Scratch) -> None:
    """ADMIN OPTION lets its holder grant itself SET, so only the running admin may hold it."""
    with _superuser() as conn:
        version = conn.execute("SHOW server_version_num").fetchone()
    if version and int(version[0]) < 160000:
        pytest.skip("per-grant INHERIT/SET options are PostgreSQL 16+")
    roles = scratch.roles
    holder = f"holder_{_suffix()}"
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.migrator)} LOGIN")
        _run(conn, f"CREATE ROLE {quote_ident(holder)} LOGIN")
        _run(
            conn,
            f"GRANT {quote_ident(roles.migrator)} TO {quote_ident(holder)} "
            "WITH ADMIN TRUE, INHERIT FALSE, SET FALSE",
        )
    try:
        with (
            psycopg.connect(
                _url("postgres", "postgres", scratch.database), autocommit=True
            ) as conn,
            pytest.raises(psycopg.errors.RaiseException, match="unexpected members"),
        ):
            run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
    finally:
        with _superuser() as conn:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(holder)}")


def test_refuses_a_pre_created_owner_keyring_holding_app(scratch: Scratch) -> None:
    """app in tracecat_owner would own public, where RLS isn't forced."""
    roles = scratch.roles
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.owner)} NOLOGIN")
        _run(conn, f"CREATE ROLE {quote_ident(roles.app)} LOGIN")
        _run(conn, f"GRANT {quote_ident(roles.owner)} TO {quote_ident(roles.app)}")
    with (
        psycopg.connect(
            _url("postgres", "postgres", scratch.database), autocommit=True
        ) as conn,
        pytest.raises(
            psycopg.errors.RaiseException, match="can act as the owner keyring"
        ),
    ):
        run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)


def test_refuses_a_pre_created_dynamic_keyring_holding_readonly(
    scratch: Scratch,
) -> None:
    roles = scratch.roles
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.dynamic_owner)} NOLOGIN")
        _run(conn, f"CREATE ROLE {quote_ident(roles.readonly)} NOLOGIN")
        _run(
            conn,
            f"GRANT {quote_ident(roles.dynamic_owner)} TO {quote_ident(roles.readonly)}",
        )
    with (
        psycopg.connect(
            _url("postgres", "postgres", scratch.database), autocommit=True
        ) as conn,
        pytest.raises(psycopg.errors.RaiseException, match="holds the dynamic keyring"),
    ):
        run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)


def test_refuses_a_database_owned_by_app(scratch: Scratch) -> None:
    """The database owner owns public (PG 15+) and could drop every moved table."""
    roles = scratch.roles
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.app)} LOGIN")
        _run(
            conn,
            f"ALTER DATABASE {quote_ident(scratch.database)} OWNER TO {quote_ident(roles.app)}",
        )
    with (
        psycopg.connect(
            _url("postgres", "postgres", scratch.database), autocommit=True
        ) as conn,
        pytest.raises(
            psycopg.errors.RaiseException, match="owner of the database or of public"
        ),
    ):
        run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
    with _superuser() as conn:
        _run(
            conn,
            f"ALTER DATABASE {quote_ident(scratch.database)} OWNER TO {quote_ident(scratch.admin)}",
        )


def test_bootstrap_ignores_functions_planted_on_the_search_path(
    scratch: Scratch,
) -> None:
    """A database-wide search_path can't swap pg_catalog.format() for a planted one."""
    roles = scratch.roles
    canary = f"canary_{_suffix()}"
    with psycopg.connect(
        _url("postgres", "postgres", scratch.database), autocommit=True
    ) as conn:
        _run(
            conn,
            "CREATE FUNCTION public.format(text, text, text) RETURNS text LANGUAGE sql "
            f"AS $f$ SELECT 'CREATE ROLE {canary}' $f$",
        )
        _run(
            conn,
            f"ALTER DATABASE {quote_ident(scratch.database)} SET search_path = public, pg_catalog",
        )
    try:
        with scratch.admin_conn() as conn:
            run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
        assert not _role_exists(canary)
    finally:
        with _superuser() as conn:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(canary)}")


def test_refuses_a_login_role_with_createrole(scratch: Scratch) -> None:
    """CREATEROLE lets a login grant itself the owner keyring."""
    roles = scratch.roles
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.app)} LOGIN CREATEROLE")
    with (
        psycopg.connect(
            _url("postgres", "postgres", scratch.database), autocommit=True
        ) as conn,
        pytest.raises(psycopg.errors.RaiseException, match="CREATEROLE"),
    ):
        run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)


def test_refuses_roles_that_own_another_database(scratch: Scratch) -> None:
    """A legacy install's login owning its database (default NULL ACL) counts too."""
    roles = scratch.roles
    other = f"{scratch.database}_owned"
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.app)} LOGIN")
        _run(
            conn, f"CREATE DATABASE {quote_ident(other)} OWNER {quote_ident(roles.app)}"
        )
    try:
        with (
            psycopg.connect(
                _url("postgres", "postgres", scratch.database), autocommit=True
            ) as conn,
            pytest.raises(psycopg.errors.RaiseException, match="another database"),
        ):
            run_bootstrap(
                conn, LoginMethod.PASSWORD, LoginPasswords(app=PASSWORD), roles
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


CREDENTIAL_NAME = re.compile(
    r"token|password|secret|hash|credential|private_key|api_key|signing|refresh",
    re.IGNORECASE,
)
# Credential-looking columns that are safe for people to read. Anything new
# that matches CREDENTIAL_NAME must go here or in READONLY_HIDDEN_COLUMNS.
SAFE_CREDENTIAL_NAMED_COLUMNS = {
    ("agent_catalog", "last_refreshed_at"),
    ("agent_custom_provider", "api_key_header"),  # a header name
    ("agent_custom_provider", "last_refreshed_at"),
    ("agent_session_command", "payload_hash"),
    ("mcp_personal_access_token", "hashed"),  # hash of a random key
    ("mcp_refresh_token", "token_hash"),  # hash of a random token
    ("oauth_integration", "use_workspace_credentials"),
    ("oauth_integration", "token_type"),
    ("oauth_integration", "token_endpoint"),
    ("oauth_integration", "token_endpoint_auth_method"),
    ("oauth_provider", "token_endpoint"),
    ("platform_registry_action", "secrets"),  # names of required secrets
    ("platform_registry_index", "secrets"),
    ("registry_action", "secrets"),
    ("registry_index", "secrets"),
    ("scim_connection", "hashed"),
    ("search_chunk", "input_hash"),
    ("search_embedding_config", "credential_id"),
    ("search_embedding_config", "credential_environment"),
    ("search_embedding_config", "input_token_limit"),
    ("service_account_api_key", "hashed"),
    ("watchtower_agent", "fingerprint_hash"),
    ("webauthn_credential", "credential_id"),
    ("webhook_api_key", "hashed"),
}


def test_every_credential_named_column_is_classified() -> None:
    """New credential columns must be hidden from readonly or marked safe.

    Encrypted columns (``encrypted_*``) are readable: their key is not in the
    database.
    """
    hidden = {(t, c) for t, cols in READONLY_HIDDEN_COLUMNS for c in cols}
    unclassified = sorted(
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if CREDENTIAL_NAME.search(column.name)
        and not column.name.startswith("encrypted_")
        and (table.name, column.name) not in hidden | SAFE_CREDENTIAL_NAMED_COLUMNS
    )
    assert unclassified == []


# --- upgrade -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LegacyInstall:
    """A single-user install: one CREATEROLE login owns everything (RDS shape)."""

    database: str
    legacy: str
    roles: RoleNames

    def url(self, user: str, password: str, driver: str = "") -> str:
        return _url(user, password, self.database, driver)

    def legacy_conn(self) -> psycopg.Connection:
        return psycopg.connect(self.url(self.legacy, "legacy"), autocommit=True)


def _alembic(url: str, *args: str) -> None:
    env = os.environ.copy()
    env["TRACECAT__DB_URI"] = url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]


def _legacy_install(suffix: str, *, migrate: bool) -> LegacyInstall:
    install = LegacyInstall(f"legacy_{suffix}", f"legacy_{suffix}", _test_roles(suffix))
    try:
        _seed_legacy_install(install, migrate=migrate)
    except BaseException:
        _drop_install(install)
        raise
    return install


def _seed_legacy_install(install: LegacyInstall, *, migrate: bool) -> None:
    with _superuser() as conn:
        _run(
            conn,
            f"CREATE ROLE {quote_ident(install.legacy)} LOGIN CREATEROLE PASSWORD 'legacy'",
        )
        _run(
            conn,
            f"CREATE DATABASE {quote_ident(install.database)} OWNER {quote_ident(install.legacy)}",
        )
    if migrate:
        with psycopg.connect(
            _url("postgres", "postgres", install.database), autocommit=True
        ) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
        _alembic(install.url(install.legacy, "legacy", "+psycopg"), "upgrade", "head")


def _drop_install(install: LegacyInstall) -> None:
    roles = install.roles
    _drop_all(install.database, [*roles.logins, *roles.keyrings, install.legacy])


def _create_dynamic_schema(conn: psycopg.Connection, schema: str) -> None:
    """What the app did before TRACECAT__DB_DYNAMIC_OWNER_ROLE: RLS on, not forced."""
    s = quote_ident(schema)
    _run(conn, f"CREATE SCHEMA {s}")
    _run(
        conn,
        f"CREATE TABLE {s}.case_fields (id uuid PRIMARY KEY, "
        'case_id uuid NOT NULL REFERENCES public."case"(id) ON DELETE CASCADE)',
    )
    _run(conn, f"ALTER TABLE {s}.case_fields ENABLE ROW LEVEL SECURITY")


def test_upgrade_moves_a_single_user_install_and_reruns_cleanly() -> None:
    """The ENG-2444 upgrade path: everything owned by one login moves to the keyrings."""
    install = _legacy_install(_suffix(), migrate=True)
    roles = install.roles
    first = f"custom_fields_ws_{uuid.uuid4().hex[:12]}"
    later = f"custom_fields_ws_{uuid.uuid4().hex[:12]}"
    passwords = LoginPasswords(PASSWORD, PASSWORD, PASSWORD)
    try:
        with install.legacy_conn() as conn:
            _create_dynamic_schema(conn, first)
            result = run_upgrade(conn, LoginMethod.PASSWORD, passwords, roles)
        assert result.complete, (
            result.failed,
            result.remaining_public,
            result.remaining_dynamic,
        )
        assert len(result.moved) > 100

        with install.legacy_conn() as conn:
            leftover = conn.execute(
                """
                SELECT c.relname FROM pg_class c
                WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r','p','v','m','S')
                  AND pg_get_userbyid(c.relowner) <> %s
                  AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e')
                """,
                (roles.owner,),
            ).fetchall()
            dynamic = conn.execute(
                "SELECT pg_get_userbyid(n.nspowner), pg_get_userbyid(c.relowner), c.relforcerowsecurity "
                "FROM pg_namespace n JOIN pg_class c ON c.relnamespace = n.oid "
                "WHERE n.nspname = %s AND c.relkind = 'r'",
                (first,),
            ).fetchall()
        assert leftover == []
        assert dynamic == [(roles.dynamic_owner, roles.dynamic_owner, True)]

        # Migrations keep working as migrator, both ways.
        migrator_url = install.url(roles.migrator, PASSWORD, "+psycopg")
        _alembic(migrator_url, "downgrade", "-1")
        _alembic(migrator_url, "upgrade", "head")

        # The app reads public and sees no dynamic rows without the bypass (forced RLS).
        with psycopg.connect(install.url(roles.app, PASSWORD)) as conn:
            assert conn.execute("SELECT count(*) FROM workflow").fetchone() == (0,)
            assert _run(
                conn, f"SELECT count(*) FROM {quote_ident(first)}.case_fields"
            ).fetchone() == (0,)

        # Rerun: nothing to do. Then a schema the old login created since (staging B0's gap).
        with install.legacy_conn() as conn:
            again = run_upgrade(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
            assert again.complete and again.moved == []
            _create_dynamic_schema(conn, later)
            catch_up = run_upgrade(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
        assert catch_up.complete
        assert catch_up.moved == [f"SCHEMA {quote_ident(later)}"]
    finally:
        _drop_install(install)


def test_upgrade_reports_a_locked_object_and_moves_the_rest() -> None:
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    try:
        with install.legacy_conn() as conn:
            conn.execute("CREATE TABLE public.busy (id int)")
            conn.execute("CREATE TABLE public.idle (id int)")
            # Uses busy with its owner's rights, like the MCP token trigger.
            conn.execute(
                "CREATE FUNCTION public.touch_busy() RETURNS void LANGUAGE sql "
                "SECURITY DEFINER AS 'DELETE FROM public.busy'"
            )
        with psycopg.connect(install.url("postgres", "postgres")) as holder:
            holder.execute("LOCK TABLE public.busy IN ACCESS SHARE MODE")
            with install.legacy_conn() as conn:
                result = run_upgrade(
                    conn,
                    LoginMethod.PASSWORD,
                    LoginPasswords(),
                    roles,
                    lock_timeout="200ms",
                    retries=0,
                )
            holder.rollback()
        assert result.moved == ["TABLE public.idle"]
        assert [label for label, _ in result.failed] == ["TABLE public.busy"]
        # The routine waits for its table, so it never runs without access to it.
        assert result.remaining_public == [
            f"TABLE public.busy (owner {install.legacy})",
            f"ROUTINE public.touch_busy() (owner {install.legacy})",
        ]
        assert not result.complete
        with install.legacy_conn() as conn:
            assert run_upgrade(
                conn, LoginMethod.PASSWORD, LoginPasswords(), roles
            ).complete
    finally:
        _drop_install(install)


def test_upgrade_bounds_lock_waits_during_bootstrap() -> None:
    """A concurrent catalog change can't stall the bootstrap phase forever.

    GRANT takes no table lock, but it waits on the row lock of a catalog tuple
    another transaction is changing (here the ``public`` schema's ACL).
    """
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    try:
        with psycopg.connect(install.url("postgres", "postgres")) as holder:
            _run(
                holder,
                f"GRANT CREATE ON SCHEMA public TO {quote_ident(install.legacy)}",
            )
            with (
                install.legacy_conn() as conn,
                pytest.raises(psycopg.errors.LockNotAvailable),
            ):
                run_upgrade(
                    conn,
                    LoginMethod.PASSWORD,
                    LoginPasswords(),
                    roles,
                    lock_timeout="200ms",
                    retries=0,
                )
            holder.rollback()
        assert not _role_exists(roles.owner)  # the bootstrap rolled back
    finally:
        _drop_install(install)


def test_upgrade_requires_an_autocommit_connection() -> None:
    with (
        psycopg.connect(_url("postgres", "postgres", "postgres")) as conn,
        pytest.raises(ValueError, match="autocommit"),
    ):
        run_upgrade(conn, LoginMethod.PASSWORD, LoginPasswords())


def test_upgrade_ignores_decoys_in_a_schema_named_after_the_admin() -> None:
    """Names stay schema-qualified, so a "$user" schema can't capture a transfer."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    decoy_owner = f"decoy_{_suffix()}"
    try:
        with _superuser() as conn:
            _run(conn, f"CREATE ROLE {quote_ident(decoy_owner)} NOLOGIN")
        with install.legacy_conn() as conn:
            conn.execute("CREATE TABLE public.genuine (id int)")
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            legacy = quote_ident(install.legacy)
            _run(
                conn, f"CREATE SCHEMA {legacy} AUTHORIZATION {quote_ident(decoy_owner)}"
            )
            _run(conn, f"CREATE VIEW {legacy}.genuine AS SELECT 1 AS id")
            _run(
                conn, f"ALTER VIEW {legacy}.genuine OWNER TO {quote_ident(decoy_owner)}"
            )
        with install.legacy_conn() as conn:
            result = run_upgrade(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
            decoy = conn.execute(
                "SELECT pg_get_userbyid(c.relowner) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relname = 'genuine'",
                (install.legacy,),
            ).fetchone()
        assert result.complete
        assert result.moved == ["TABLE public.genuine"]
        assert decoy == (decoy_owner,)
    finally:
        _drop_install(install)
        with _superuser() as conn:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(decoy_owner)}")


def test_upgrade_is_incomplete_when_from_names_the_wrong_owner() -> None:
    """Completion means nothing in public is off the owner keyring, whatever --from says."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    bystander = f"bystander_{_suffix()}"
    try:
        with _superuser() as conn:
            _run(conn, f"CREATE ROLE {quote_ident(bystander)} NOLOGIN")
        with install.legacy_conn() as conn:
            conn.execute("CREATE TABLE public.kept (id int)")
            result = run_upgrade(
                conn,
                LoginMethod.PASSWORD,
                LoginPasswords(),
                roles,
                old_owners=[bystander],
            )
            with pytest.raises(ValueError, match="don't exist"):
                run_upgrade(
                    conn,
                    LoginMethod.PASSWORD,
                    LoginPasswords(),
                    roles,
                    old_owners=["no_such_role_anywhere"],
                )
        assert result.moved == []
        assert result.remaining_public == [
            f"TABLE public.kept (owner {install.legacy})"
        ]
        assert not result.complete
    finally:
        _drop_install(install)
        with _superuser() as conn:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(bystander)}")


def test_cli_output_escapes_control_characters() -> None:
    assert _printable("TABLE public.\x1b[31mevil") == "TABLE public.\\x1b[31mevil"


def test_upgrade_keeps_the_old_login_working_until_cutover() -> None:
    """A separate admin moves --from's objects; the app still on that login keeps access."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    try:
        with install.legacy_conn() as conn:
            conn.execute("CREATE TABLE public.orders (id int)")
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            result = run_upgrade(
                conn,
                LoginMethod.PASSWORD,
                LoginPasswords(),
                roles,
                old_owners=[install.legacy],
            )
        assert result.complete
        assert (
            f"{install.legacy}: kept access through {roles.owner} until cutover"
            in result.notes
        )
        with install.legacy_conn() as conn:
            conn.execute("INSERT INTO public.orders VALUES (1)")
            assert conn.execute("SELECT count(*) FROM public.orders").fetchone() == (1,)
    finally:
        _drop_install(install)


def test_upgrade_reenables_rls_on_dynamic_tables_with_policies() -> None:
    """A policy with RLS switched off protects nothing; the upgrade turns it back on."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    schema = quote_ident(f"tables_ws_{uuid.uuid4().hex[:12]}")
    try:
        with install.legacy_conn() as conn:
            _run(conn, f"CREATE SCHEMA {schema}")
            _run(conn, f"CREATE TABLE {schema}.t (id int)")
            _run(conn, f"CREATE POLICY p ON {schema}.t USING (false)")
            _run(conn, f"ALTER TABLE {schema}.t DISABLE ROW LEVEL SECURITY")
            result = run_upgrade(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
            flags = _run(
                conn,
                f"SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                f"WHERE oid = '{schema}.t'::regclass",
            ).fetchone()
        assert result.complete
        assert flags == (True, True)
    finally:
        _drop_install(install)


def test_upgrade_reports_superuser_owned_objects_it_skips() -> None:
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    try:
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            # RDS keeps rdsadmin-owned functions in public; the admin can't move them.
            conn.execute(
                "CREATE FUNCTION public.system_owned() RETURNS int LANGUAGE sql AS 'SELECT 1'"
            )
        with install.legacy_conn() as conn:
            result = run_upgrade(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
        assert result.complete
        assert any(
            note.startswith("Not moved (owned by a superuser") and "postgres: 1" in note
            for note in result.notes
        )
    finally:
        _drop_install(install)


def test_upgrade_validates_from_before_changing_anything() -> None:
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    try:
        with (
            install.legacy_conn() as conn,
            pytest.raises(ValueError, match="don't exist"),
        ):
            run_upgrade(
                conn,
                LoginMethod.PASSWORD,
                LoginPasswords(),
                roles,
                old_owners=["no_such_role_anywhere"],
            )
        assert not _role_exists(roles.owner)
    finally:
        _drop_install(install)


def _superuser_upgrade(install: LegacyInstall, old_owners: list[str]) -> UpgradeResult:
    """Upgrade as a separate superuser admin, moving ``old_owners``' objects."""
    with psycopg.connect(install.url("postgres", "postgres"), autocommit=True) as conn:
        return run_upgrade(
            conn, LoginMethod.PASSWORD, LoginPasswords(), install.roles, old_owners
        )


def test_upgrade_from_an_existing_app_owner_keeps_app_working() -> None:
    """ALTER OWNER rewrites app's own ACL; each transfer gives the grants back at once.

    Nothing re-grants after the pass, so app working afterwards means it never
    lost access between transfers.
    """
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    try:
        with _superuser() as conn:
            _run(
                conn,
                f"CREATE ROLE {quote_ident(roles.app)} LOGIN PASSWORD {quote_literal(PASSWORD)}",
            )
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            conn.execute("CREATE TABLE public.items (id serial)")
            _run(conn, f"ALTER TABLE public.items OWNER TO {quote_ident(roles.app)}")
        result = _superuser_upgrade(install, old_owners=[roles.app])
        assert result.complete
        with psycopg.connect(install.url(roles.app, PASSWORD)) as conn:
            conn.execute("INSERT INTO public.items DEFAULT VALUES")  # uses the sequence
            assert conn.execute("SELECT count(*) FROM public.items").fetchone() == (1,)
    finally:
        _drop_install(install)


def test_routines_wait_for_tables_owned_by_roles_not_in_from() -> None:
    install = _legacy_install(_suffix(), migrate=False)
    try:
        with install.legacy_conn() as conn:
            conn.execute("CREATE TABLE public.tokens (id int)")
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            conn.execute(
                "CREATE FUNCTION public.revoke_tokens() RETURNS void LANGUAGE sql "
                "SECURITY DEFINER AS 'DELETE FROM public.tokens'"
            )
        result = _superuser_upgrade(install, old_owners=["postgres"])
        assert "ROUTINE public.revoke_tokens()" not in result.moved
        assert (
            f"TABLE public.tokens (owner {install.legacy})" in result.remaining_public
        )
        assert not result.complete
    finally:
        _drop_install(install)


def test_superuser_owned_tables_always_count_as_unmoved() -> None:
    """Only routines and types get the superuser carve-out (RDS's rdsadmin functions)."""
    install = _legacy_install(_suffix(), migrate=False)
    try:
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            conn.execute("CREATE TABLE public.app_data (id int)")
        result = _superuser_upgrade(install, old_owners=[install.legacy])
        assert result.remaining_public == ["TABLE public.app_data (owner postgres)"]
        assert not result.complete
    finally:
        _drop_install(install)


def test_upgrade_only_keeps_access_for_owners_of_moved_objects() -> None:
    """A --from role that owns nothing must not be handed the owner keyring."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    analyst = f"analyst_{_suffix()}"
    try:
        with _superuser() as conn:
            _run(conn, f"CREATE ROLE {quote_ident(analyst)} LOGIN")
        result = _superuser_upgrade(install, [analyst])
        with _superuser() as conn:
            member = conn.execute(
                "SELECT pg_has_role(%s, %s, 'MEMBER')", (analyst, roles.owner)
            ).fetchone()
        assert member == (False,)
        assert result.kept_access == []
    finally:
        _drop_install(install)
        with _superuser() as conn:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(analyst)}")


def test_views_wait_for_the_tables_they_read() -> None:
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    try:
        with install.legacy_conn() as conn:
            conn.execute("CREATE TABLE public.busy (id int)")
            conn.execute("CREATE VIEW public.busy_view AS SELECT id FROM public.busy")
        with psycopg.connect(install.url("postgres", "postgres")) as holder:
            holder.execute("LOCK TABLE public.busy IN ACCESS SHARE MODE")
            with install.legacy_conn() as conn:
                result = run_upgrade(
                    conn,
                    LoginMethod.PASSWORD,
                    LoginPasswords(),
                    roles,
                    lock_timeout="200ms",
                    retries=0,
                )
            holder.rollback()
        assert "VIEW public.busy_view" not in result.moved
        assert (
            f"VIEW public.busy_view (owner {install.legacy})" in result.remaining_public
        )
    finally:
        _drop_install(install)


def test_upgrade_reports_objects_the_admin_cannot_act_as() -> None:
    """The admin is never granted the owner's role (that's the rds_iam lockout path)."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    stranger = f"stranger_{_suffix()}"
    schema = f"tables_ws_{uuid.uuid4().hex[:12]}"
    try:
        with _superuser() as conn:
            _run(conn, f"CREATE ROLE {quote_ident(stranger)} NOLOGIN")
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            _run(
                conn,
                f"CREATE SCHEMA {quote_ident(schema)} AUTHORIZATION {quote_ident(stranger)}",
            )
        with install.legacy_conn() as conn:
            result = run_upgrade(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
            member = conn.execute(
                "SELECT pg_has_role(%s, %s, 'MEMBER')", (install.legacy, stranger)
            ).fetchone()
        assert [label for label, _ in result.failed] == [
            f"SCHEMA {quote_ident(schema)}"
        ]
        assert result.remaining_dynamic == [schema]
        assert member == (False,)
    finally:
        _drop_install(install)
        with _superuser() as conn:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(stranger)}")


def test_dynamic_owners_keep_access_even_when_not_named_in_from() -> None:
    install = _legacy_install(_suffix(), migrate=False)
    schema = quote_ident(f"tables_ws_{uuid.uuid4().hex[:12]}")
    try:
        with install.legacy_conn() as conn:
            _run(conn, f"CREATE SCHEMA {schema}")
            _run(conn, f"CREATE TABLE {schema}.t (id int)")
        result = _superuser_upgrade(install, ["postgres"])
        assert result.complete
        # Dynamic-only owners get the dynamic keyring, not public ownership.
        assert result.kept_access == [(install.roles.dynamic_owner, install.legacy)]
        with install.legacy_conn() as conn:
            _run(conn, f"INSERT INTO {schema}.t VALUES (1)")
    finally:
        _drop_install(install)


def test_upgrade_moves_dynamic_schemas_the_owner_keyring_created() -> None:
    """Migrations create some dynamic schemas as the owner keyring: no self-grant."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    schema = quote_ident(f"tables_ws_{uuid.uuid4().hex[:12]}")
    try:
        with install.legacy_conn() as conn:
            run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
            _run(conn, f"SET ROLE {quote_ident(roles.owner)}")
            _run(conn, f"CREATE SCHEMA {schema}")
            _run(conn, f"CREATE TABLE {schema}.t (id int)")
        result = _superuser_upgrade(install, [install.legacy])
        assert result.failed == []
        assert result.complete
        assert f"SCHEMA {schema}" in result.moved
    finally:
        _drop_install(install)


def test_keep_access_refuses_a_group_that_contains_a_topology_role() -> None:
    """Granting the owner keyring to a group holding readonly would lift readonly above RLS."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    group = f"legacy_group_{_suffix()}"
    try:
        with install.legacy_conn() as conn:
            run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
        with _superuser() as conn:
            _run(conn, f"CREATE ROLE {quote_ident(group)} NOLOGIN")
            _run(conn, f"GRANT {quote_ident(group)} TO {quote_ident(roles.readonly)}")
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            conn.execute("CREATE TABLE public.shared (id int)")
            _run(conn, f"ALTER TABLE public.shared OWNER TO {quote_ident(group)}")
        result = _superuser_upgrade(install, [group])
        with _superuser() as conn:
            reach = conn.execute(
                "SELECT pg_has_role(%s, %s, 'MEMBER')", (roles.readonly, roles.owner)
            ).fetchone()
        assert reach == (False,)
        assert [label for label, _ in result.failed] == [
            f"KEEP ACCESS {quote_ident(group)}"
        ]
        assert not result.complete
    finally:
        _drop_install(install)
        with _superuser() as conn:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(group)}")


def test_keep_access_refuses_a_dynamic_group_that_contains_readonly() -> None:
    """Indirect reach counts: readonly in a dynamic owner's group must not get app_owner."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    group = f"dyn_group_{_suffix()}"
    schema = quote_ident(f"tables_ws_{uuid.uuid4().hex[:12]}")
    try:
        with install.legacy_conn() as conn:
            run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
        with _superuser() as conn:
            _run(conn, f"CREATE ROLE {quote_ident(group)} NOLOGIN")
            _run(conn, f"GRANT {quote_ident(group)} TO {quote_ident(roles.readonly)}")
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            _run(conn, f"CREATE SCHEMA {schema} AUTHORIZATION {quote_ident(group)}")
        result = _superuser_upgrade(install, [install.legacy])
        with _superuser() as conn:
            reach = conn.execute(
                "SELECT pg_has_role(%s, %s, 'MEMBER')",
                (roles.readonly, roles.dynamic_owner),
            ).fetchone()
        assert reach == (False,)
        assert f"KEEP ACCESS {quote_ident(group)}" in [
            label for label, _ in result.failed
        ]
        assert not result.complete
    finally:
        _drop_install(install)
        with _superuser() as conn:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(group)}")


def test_cutover_revoke_names_the_role_that_holds_the_grant() -> None:
    """A group role kept access for its member logins; revoking from the group removes it."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    group = f"legacy_group_{_suffix()}"
    try:
        with _superuser() as conn:
            _run(conn, f"CREATE ROLE {quote_ident(group)} NOLOGIN")
            _run(conn, f"GRANT {quote_ident(group)} TO {quote_ident(install.legacy)}")
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            conn.execute("CREATE TABLE public.shared (id int)")
            _run(conn, f"ALTER TABLE public.shared OWNER TO {quote_ident(group)}")
        result = _superuser_upgrade(install, [group])
        assert result.complete
        assert result.kept_access == [(roles.owner, group)]
        assert any(
            f"REVOKE {quote_ident(roles.owner)} FROM {quote_ident(group)}" in note
            for note in result.notes
        )
    finally:
        _drop_install(install)
        with _superuser() as conn:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(group)}")


def test_upgrade_role_names_cannot_escape_literals() -> None:
    """A database-level standard_conforming_strings=off can't turn a role name into SQL."""
    install = _legacy_install(_suffix(), migrate=False)
    canary = f"canary_{uuid.uuid4().hex[:8]}"
    crafted = f"\\'); CREATE ROLE {canary}; --"
    schema = quote_ident(f"tables_ws_{uuid.uuid4().hex[:12]}")
    try:
        with _superuser() as conn:
            _run(conn, 'CREATE ROLE "\'" NOLOGIN')  # makes the injected prefix valid
            _run(conn, f"CREATE ROLE {quote_ident(crafted)} NOLOGIN")
            _run(
                conn,
                f"ALTER DATABASE {quote_ident(install.database)} SET standard_conforming_strings = off",
            )
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            _run(conn, f"CREATE SCHEMA {schema} AUTHORIZATION {quote_ident(crafted)}")
        _superuser_upgrade(install, [install.legacy])
        assert not _role_exists(canary)
    finally:
        _drop_install(install)
        with _superuser() as conn:
            for role in (crafted, "'", canary):
                _run(conn, f"DROP ROLE IF EXISTS {quote_ident(role)}")


def test_refuses_a_public_schema_owned_by_app(scratch: Scratch) -> None:
    roles = scratch.roles
    with _superuser() as conn:
        _run(conn, f"CREATE ROLE {quote_ident(roles.app)} LOGIN")
    with psycopg.connect(
        _url("postgres", "postgres", scratch.database), autocommit=True
    ) as conn:
        _run(conn, f"ALTER SCHEMA public OWNER TO {quote_ident(roles.app)}")
        with pytest.raises(
            psycopg.errors.RaiseException, match="owner of the database or of public"
        ):
            run_bootstrap(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
        _run(conn, "ALTER SCHEMA public OWNER TO pg_database_owner")


def test_upgrade_keeps_app_execute_on_routines_it_owned() -> None:
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    try:
        with _superuser() as conn:
            _run(
                conn,
                f"CREATE ROLE {quote_ident(roles.app)} LOGIN PASSWORD {quote_literal(PASSWORD)}",
            )
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            conn.execute(
                "CREATE FUNCTION public.helper() RETURNS int LANGUAGE sql AS 'SELECT 1'"
            )
            conn.execute("REVOKE EXECUTE ON FUNCTION public.helper() FROM PUBLIC")
            _run(
                conn,
                f"ALTER FUNCTION public.helper() OWNER TO {quote_ident(roles.app)}",
            )
        result = _superuser_upgrade(install, [roles.app])
        assert "ROUTINE public.helper()" in result.moved
        with psycopg.connect(install.url(roles.app, PASSWORD)) as conn:
            assert conn.execute("SELECT public.helper()").fetchone() == (1,)
    finally:
        _drop_install(install)


def test_upgrade_ignores_operators_planted_on_the_search_path() -> None:
    """The whole run resolves from pg_catalog, reports included: no admin SQL via public.=."""
    install = _legacy_install(_suffix(), migrate=False)
    canary = f"canary_{uuid.uuid4().hex[:8]}"
    try:
        with psycopg.connect(
            install.url("postgres", "postgres"), autocommit=True
        ) as conn:
            _run(
                conn,
                "CREATE FUNCTION public.leak(oid, oid) RETURNS boolean LANGUAGE plpgsql AS "
                f"$f$ BEGIN EXECUTE 'CREATE ROLE {canary}'; RETURN $1 OPERATOR(pg_catalog.=) $2; "
                "EXCEPTION WHEN duplicate_object THEN RETURN $1 OPERATOR(pg_catalog.=) $2; END $f$",
            )
            conn.execute(
                "CREATE OPERATOR public.= (LEFTARG = oid, RIGHTARG = oid, FUNCTION = public.leak)"
            )
            _run(
                conn,
                f"ALTER DATABASE {quote_ident(install.database)} SET search_path = public, pg_catalog",
            )
            conn.execute("CREATE TABLE public.t (id int)")
        _superuser_upgrade(install, ["postgres"])
        assert not _role_exists(canary)
    finally:
        _drop_install(install)
        with _superuser() as conn:
            _run(conn, f"DROP ROLE IF EXISTS {quote_ident(canary)}")


def test_upgrade_grants_app_execute_on_legacy_helpers() -> None:
    """A hardened install revoked PUBLIC EXECUTE; app needs it once it is the login."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    try:
        with install.legacy_conn() as conn:
            conn.execute(
                "CREATE FUNCTION public.helper() RETURNS int LANGUAGE sql AS 'SELECT 1'"
            )
            conn.execute("REVOKE EXECUTE ON FUNCTION public.helper() FROM PUBLIC")
            # SECURITY DEFINER would run as the owner keyring: app must not get it.
            conn.execute(
                "CREATE FUNCTION public.privileged() RETURNS int LANGUAGE sql "
                "SECURITY DEFINER AS 'SELECT 2'"
            )
            conn.execute("REVOKE EXECUTE ON FUNCTION public.privileged() FROM PUBLIC")
            result = run_upgrade(
                conn, LoginMethod.PASSWORD, LoginPasswords(app=PASSWORD), roles
            )
        assert result.complete
        with psycopg.connect(install.url(roles.app, PASSWORD)) as conn:
            assert conn.execute("SELECT public.helper()").fetchone() == (1,)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT public.privileged()")
    finally:
        _drop_install(install)


def test_upgrade_moves_routines_named_like_the_dollar_quote_tag() -> None:
    """A catalog name carrying the DO block's tag must not end the block early."""
    install = _legacy_install(_suffix(), migrate=False)
    try:
        with install.legacy_conn() as conn:
            conn.execute(
                'CREATE FUNCTION public."helper$bootstrap$x"() RETURNS int '
                "LANGUAGE sql AS 'SELECT 1'"
            )
            result = run_upgrade(
                conn, LoginMethod.PASSWORD, LoginPasswords(), install.roles
            )
        assert result.failed == []
        assert result.complete
    finally:
        _drop_install(install)


# --- upgrade on the shapes it must handle before it runs by default ------------


def test_upgrade_on_a_compose_shaped_install() -> None:
    """Compose today: the postgres superuser migrated and owns everything; it upgrades itself."""
    suffix = _suffix()
    roles = _test_roles(suffix)
    database = f"compose_{suffix}"
    schema = f"custom_fields_ws_{uuid.uuid4().hex[:12]}"
    passwords = LoginPasswords(PASSWORD, PASSWORD, PASSWORD)
    with _superuser() as conn:
        _run(conn, f"CREATE DATABASE {quote_ident(database)}")
    try:
        superuser = _url("postgres", "postgres", database)
        _alembic(_url("postgres", "postgres", database, "+psycopg"), "upgrade", "head")
        with psycopg.connect(superuser, autocommit=True) as conn:
            _create_dynamic_schema(conn, schema)
            first = run_upgrade(conn, LoginMethod.PASSWORD, passwords, roles)
            again = run_upgrade(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
            owners = conn.execute(
                """
                SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c
                WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r','p','v','m','S')
                  AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e')
                """
            ).fetchall()
        assert first.complete and len(first.moved) > 100
        assert again.complete and again.moved == [] and again.failed == []
        assert owners == [(roles.owner,)]
        assert first.kept_access == []  # a superuser admin needs no kept access
        _alembic(
            _url(roles.migrator, PASSWORD, database, "+psycopg"), "downgrade", "-1"
        )
        _alembic(
            _url(roles.migrator, PASSWORD, database, "+psycopg"), "upgrade", "head"
        )
        with psycopg.connect(_url(roles.app, PASSWORD, database)) as conn:
            assert conn.execute("SELECT count(*) FROM workflow").fetchone() == (0,)
            assert _run(
                conn, f"SELECT count(*) FROM {quote_ident(schema)}.case_fields"
            ).fetchone() == (0,)
    finally:
        _drop_all(database, [*roles.logins, *roles.keyrings])


def test_upgrade_on_a_staging_b0_shaped_install() -> None:
    """Staging after B0: app and app_owner exist, one dynamic schema already moved, one not."""
    install = _legacy_install(_suffix(), migrate=True)
    roles = install.roles
    q = quote_ident
    moved = f"tables_ws_{uuid.uuid4().hex[:12]}"
    later = f"custom_fields_ws_{uuid.uuid4().hex[:12]}"
    try:
        with install.legacy_conn() as conn:
            # B0 steps 1-4, as the master (here the CREATEROLE legacy login).
            _run(
                conn,
                f"CREATE ROLE {q(roles.app)} LOGIN PASSWORD {quote_literal(PASSWORD)}",
            )
            _run(
                conn,
                f"GRANT CONNECT, CREATE ON DATABASE {q(install.database)} TO {q(roles.app)}",
            )
            _run(conn, f"GRANT USAGE ON SCHEMA public TO {q(roles.app)}")
            _run(
                conn,
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {q(roles.app)}",
            )
            _run(
                conn,
                f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {q(roles.app)}",
            )
            _run(conn, f"CREATE ROLE {q(roles.dynamic_owner)} NOLOGIN")
            _run(
                conn,
                f"GRANT {q(roles.dynamic_owner)} TO {q(install.legacy)} WITH INHERIT TRUE, SET TRUE",
            )
            _run(
                conn,
                f"GRANT {q(roles.dynamic_owner)} TO {q(roles.app)} WITH INHERIT TRUE, SET TRUE",
            )
            _run(
                conn,
                f"GRANT CREATE ON DATABASE {q(install.database)} TO {q(roles.dynamic_owner)}",
            )
            _run(
                conn,
                f'GRANT REFERENCES ON TABLE public."case" TO {q(roles.dynamic_owner)}',
            )
            _create_dynamic_schema(conn, moved)
            _run(conn, f"ALTER SCHEMA {q(moved)} OWNER TO {q(roles.dynamic_owner)}")
            _run(
                conn,
                f"ALTER TABLE {q(moved)}.case_fields OWNER TO {q(roles.dynamic_owner)}",
            )
            _run(conn, f"ALTER TABLE {q(moved)}.case_fields FORCE ROW LEVEL SECURITY")
            # Created after B0 by the app still running as the master.
            _create_dynamic_schema(conn, later)
            result = run_upgrade(
                conn,
                LoginMethod.PASSWORD,
                LoginPasswords(PASSWORD, None, PASSWORD),
                roles,
            )
            again = run_upgrade(conn, LoginMethod.PASSWORD, LoginPasswords(), roles)
        assert result.complete, (
            result.failed,
            result.remaining_public,
            result.remaining_dynamic,
        )
        assert f"SCHEMA {q(later)}" in result.moved
        assert f"SCHEMA {q(moved)}" not in result.moved
        assert again.complete and again.moved == []
        with psycopg.connect(
            install.url(roles.app, PASSWORD)
        ) as conn:  # B0's app, password untouched
            assert conn.execute("SELECT count(*) FROM workflow").fetchone() == (0,)
            assert _run(
                conn, f"SELECT count(*) FROM {q(later)}.case_fields"
            ).fetchone() == (0,)
        with install.legacy_conn() as conn:  # the master keeps password login
            assert conn.execute("SELECT count(*) FROM workflow").fetchone() == (0,)
    finally:
        _drop_install(install)


def test_a_failed_upgrade_leaves_the_old_login_working() -> None:
    """Mid-way failure (a held lock): the old login still reads and writes everything."""
    install = _legacy_install(_suffix(), migrate=False)
    roles = install.roles
    try:
        with install.legacy_conn() as conn:
            conn.execute("CREATE TABLE public.busy (id serial)")
            conn.execute("CREATE TABLE public.idle (id serial)")
        with psycopg.connect(install.url("postgres", "postgres")) as holder:
            holder.execute("LOCK TABLE public.busy IN ACCESS SHARE MODE")
            with psycopg.connect(
                install.url("postgres", "postgres"), autocommit=True
            ) as conn:
                result = run_upgrade(
                    conn,
                    LoginMethod.PASSWORD,
                    LoginPasswords(),
                    roles,
                    old_owners=[install.legacy],
                    lock_timeout="200ms",
                    retries=0,
                )
            holder.rollback()
        assert not result.complete
        assert result.kept_access == [(roles.owner, install.legacy)]
        assert "TABLE public.idle" in result.moved
        with install.legacy_conn() as conn:
            for table in ("busy", "idle"):
                _run(conn, f"INSERT INTO public.{table} DEFAULT VALUES")
                assert _run(
                    conn, f"SELECT count(*) FROM public.{table}"
                ).fetchone() == (1,)
        rerun = _superuser_upgrade(install, [install.legacy])
        assert rerun.complete
        assert rerun.kept_access == [(roles.owner, install.legacy)]
    finally:
        _drop_install(install)


# --- migrate (the migrations container's entrypoint) ---------------------------


def _fresh_database(suffix: str) -> str:
    database = f"migrate_{suffix}"
    with _superuser() as conn:
        _run(conn, f"CREATE DATABASE {quote_ident(database)}")
    return database


def _configure_migrate(
    monkeypatch: pytest.MonkeyPatch, database: str, *, admin: bool
) -> None:
    superuser = _url("postgres", "postgres", database, "+psycopg")
    monkeypatch.setenv("TRACECAT__DB_URI", superuser)
    monkeypatch.setattr(config, "TRACECAT__DB_ADMIN_URI", superuser if admin else None)
    monkeypatch.setattr(config, "TRACECAT__DB_SSLMODE", "disable")
    monkeypatch.setattr(config, "TRACECAT__DB_ROLE_LOGIN", "password")
    monkeypatch.setattr(config, "TRACECAT__DB_MIGRATOR_PASSWORD", PASSWORD)
    monkeypatch.setattr(config, "TRACECAT__DB_APP_PASSWORD", PASSWORD)
    monkeypatch.setattr(config, "TRACECAT__DB_AGENT_RUNTIME_PASSWORD", None)


def _alembic_head(database: str) -> tuple[str, ...] | None:
    with psycopg.connect(_url("postgres", "postgres", database)) as conn:
        return conn.execute("SELECT version_num FROM public.alembic_version").fetchone()


def test_migrate_without_an_admin_runs_alembic_as_the_configured_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No admin credential: exactly today's behaviour, nothing else changes."""
    suffix = _suffix()
    roles = _test_roles(suffix)
    database = _fresh_database(suffix)
    try:
        _configure_migrate(monkeypatch, database, admin=False)
        assert migrate(roles) == 0
        assert _alembic_head(database) is not None
        assert not _role_exists(roles.owner)
    finally:
        _drop_all(database, [*roles.logins, *roles.keyrings])


def test_migrate_sets_up_roles_on_a_fresh_compose_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin = the Compose postgres superuser: roles, migrations as migrator, app logs in."""
    suffix = _suffix()
    roles = _test_roles(suffix)
    database = _fresh_database(suffix)
    try:
        _configure_migrate(monkeypatch, database, admin=True)
        assert migrate(roles) == 0
        with psycopg.connect(_url("postgres", "postgres", database)) as conn:
            owners = conn.execute(
                """
                SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c
                WHERE c.relnamespace = 'public'::regnamespace
                  AND c.relkind IN ('r','p','v','m','S')
                  AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e')
                """
            ).fetchall()
            references = conn.execute(
                """SELECT has_table_privilege(%s, 'public."case"', 'REFERENCES')""",
                (roles.dynamic_owner,),
            ).fetchone()
        assert owners == [(roles.owner,)]  # migrator created everything as the keyring
        assert references == (True,)  # granted after migrations created "case"
        with psycopg.connect(_url(roles.app, PASSWORD, database)) as conn:
            assert conn.execute("SELECT count(*) FROM workflow").fetchone() == (0,)
        assert migrate(roles) == 0  # every deploy reruns it
    finally:
        _drop_all(database, [*roles.logins, *roles.keyrings])


def test_migrate_runs_alembic_as_the_admin_when_role_setup_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transfer that can't finish yet must not block the deploy."""
    suffix = _suffix()
    roles = _test_roles(suffix)
    database = _fresh_database(suffix)
    try:
        _configure_migrate(monkeypatch, database, admin=True)
        with psycopg.connect(
            _url("postgres", "postgres", database), autocommit=True
        ) as conn:
            conn.execute("CREATE TABLE public.busy (id int)")
        with psycopg.connect(_url("postgres", "postgres", database)) as holder:
            holder.execute("LOCK TABLE public.busy IN ACCESS SHARE MODE")
            assert migrate(roles, lock_timeout="200ms", retries=0) == 0
            holder.rollback()
        assert _alembic_head(database) is not None
    finally:
        _drop_all(database, [*roles.logins, *roles.keyrings])


def test_migrations_ignore_a_shadow_alembic_version_in_a_user_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """migrator runs as tracecat_owner; a "$user" schema of that name must not be read."""
    suffix = _suffix()
    roles = _test_roles(suffix)
    database = _fresh_database(suffix)
    try:
        _configure_migrate(monkeypatch, database, admin=True)
        assert migrate(roles) == 0
        head = _alembic_head(database)
        with psycopg.connect(
            _url(roles.app, PASSWORD, database), autocommit=True
        ) as conn:
            # app has CREATE on the database (it creates workspace schemas).
            _run(conn, f"CREATE SCHEMA {quote_ident(roles.owner)}")
            _run(
                conn,
                f"CREATE TABLE {quote_ident(roles.owner)}.alembic_version (version_num varchar(32))",
            )
            _run(
                conn,
                f"INSERT INTO {quote_ident(roles.owner)}.alembic_version VALUES ('ffffffffffff')",
            )
            _run(
                conn,
                f"GRANT USAGE ON SCHEMA {quote_ident(roles.owner)} TO {quote_ident(roles.owner)}",
            )
            _run(
                conn,
                f"GRANT ALL ON {quote_ident(roles.owner)}.alembic_version TO {quote_ident(roles.owner)}",
            )
        # Alembic as migrator would fail on the unknown revision if it read the shadow.
        assert migrate(roles) == 0
        assert _alembic_head(database) == head
    finally:
        with _superuser() as conn:
            _run(conn, f"DROP DATABASE IF EXISTS {quote_ident(database)} WITH (FORCE)")
        _drop_all(database, [*roles.logins, *roles.keyrings])
