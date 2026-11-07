"""Postgres role topology: the bootstrap command and the enforce-mode login check.

Every install uses the same roles; only how the login roles authenticate
changes per install.

Keyrings (NOLOGIN, never in ``rds_iam``) own objects:

- ``tracecat_owner`` owns ``public``. It is a member of ``app_owner``.
- ``app_owner`` owns the dynamic ``tables_*`` / ``custom_fields_*`` schemas
  (``TRACECAT__DB_DYNAMIC_OWNER_ROLE``).
- ``tracecat_readonly`` holds ``SELECT`` on ``public`` for people, except the
  columns in :data:`READONLY_HIDDEN_COLUMNS`. Each bootstrap run grants it
  the tables that exist then; there is no auto-grant for future tables.

Login roles are members of keyrings, never the other way round:

- ``migrator`` runs migrations as ``tracecat_owner`` (``SET role`` at login).
- ``app`` runs every app component. It has DML on ``public`` and owner rights
  on dynamic tables through ``app_owner``. FORCE RLS still applies to it.
- ``agent_runtime`` is the agent runtime's login. ``app`` is a member of it
  (the one login-to-login edge): the worker must be able to signal the
  runtime's backends during a takeover, and ``app`` already holds everything
  the runtime is granted. Its table grants ship with ENG-2315.

On RDS, any role that is a member of an ``rds_iam`` role, even indirectly or
with ``INHERIT FALSE``, loses password login. Because memberships only point
from login roles to keyrings, the admin can join ``tracecat_owner`` and keep
its password whatever login method the login roles use. The final check
refuses (and rolls back) any state where the admin or a keyring would be in
``rds_iam``, or a password login role still is.

Roles are cluster-wide, so the bootstrap refuses to run when a Tracecat role
already has privileges on another database: one Tracecat install per cluster.

Run as an admin (a superuser, or a ``CREATEROLE`` role such as the RDS master)::

    TRACECAT__DB_ADMIN_URI=postgresql://admin:...@host/db \\
    TRACECAT__DB_MIGRATOR_PASSWORD=... TRACECAT__DB_APP_PASSWORD=... \\
        python -m tracecat.db.roles bootstrap --login password

``--sql`` prints the same statements, without passwords, for databases where
the command can't run.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import psycopg
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection

from tracecat import config

DYNAMIC_OWNER_ENV_VAR = "TRACECAT__DB_DYNAMIC_OWNER_ROLE"


class LoginMethod(StrEnum):
    """How the login roles authenticate on this install."""

    PASSWORD = "password"
    AWS_IAM = "aws_iam"
    AZURE_ENTRA = "azure_entra"


@dataclass(frozen=True, slots=True)
class RoleNames:
    """Names of the topology's roles. Only tests change them."""

    owner: str = "tracecat_owner"
    dynamic_owner: str = "app_owner"
    readonly: str = "tracecat_readonly"
    migrator: str = "migrator"
    app: str = "app"
    agent_runtime: str = "agent_runtime"
    iam: str = "rds_iam"
    """RDS's IAM-login role, when it exists."""

    @property
    def keyrings(self) -> tuple[str, str, str]:
        return (self.owner, self.dynamic_owner, self.readonly)

    @property
    def logins(self) -> tuple[str, str, str]:
        return (self.migrator, self.app, self.agent_runtime)


# Honour an existing TRACECAT__DB_DYNAMIC_OWNER_ROLE so the app and the
# bootstrap agree on the role that owns dynamic tables.
READONLY_HIDDEN_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("access_token", ("token",)),
    ("oauth_account", ("access_token", "refresh_token")),
    ("invitation", ("token",)),
    ("organization_invitation", ("token",)),
    ("organization_domain", ("verification_token",)),
    ("user", ("hashed_password",)),
    ("mfa_recovery_code", ("code_hash",)),
    # Legacy rows keep Slack tokens and secrets in plaintext.
    ("agent_channel_token", ("config",)),
)
"""Columns ``tracecat_readonly`` never reads: bearer tokens stored in plaintext
and crackable password or recovery-code hashes. Encrypted columns stay
readable because their key is not in the database. Add new credential columns
here."""

DEFAULT_ROLES = RoleNames(
    dynamic_owner=config.TRACECAT__DB_DYNAMIC_OWNER_ROLE or "app_owner"
)


class UnsafeDatabaseLoginError(RuntimeError):
    """The app's database login can bypass RLS, so enforce mode can't start."""


def quote_ident(name: str) -> str:
    """Quote a Postgres identifier."""
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """Quote a Postgres string literal (``standard_conforming_strings`` on)."""
    return "'" + value.replace("'", "''") + "'"


def _do(body: str) -> str:
    return f"DO $bootstrap$\nBEGIN\n{body}\nEND\n$bootstrap$;"


def _create_role(name: str, *, login: bool) -> list[str]:
    kind = "LOGIN" if login else "NOLOGIN"
    create = _do(
        f"  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = {quote_literal(name)}) THEN\n"
        f"    CREATE ROLE {quote_ident(name)} {kind};\n"
        "  END IF;"
    )
    return [create, f"ALTER ROLE {quote_ident(name)} {kind};"]


def _require_role(name: str) -> str:
    """Fail unless an externally managed login role (Azure Entra) exists."""
    return _do(
        f"  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = {quote_literal(name)}) THEN\n"
        f"    RAISE EXCEPTION 'Role % does not exist', {quote_literal(name)}\n"
        "      USING HINT = 'Create it first with pgaadauth_create_principal_with_oid, then rerun bootstrap.';\n"
        "  END IF;"
    )


def _grant_membership(role: str, member: str) -> str:
    """Grant membership with INHERIT and SET.

    PG 16 records INHERIT/SET per grant and a CREATEROLE admin's automatic
    self-grant has neither, so state them explicitly there. Older versions
    take inheritance from the member role.
    """
    args = f"{quote_literal(role)}, {quote_literal(member)}"
    return _do(
        "  IF pg_catalog.current_setting('server_version_num')::int >= 160000 THEN\n"
        f"    EXECUTE format('GRANT %I TO %I WITH INHERIT TRUE, SET TRUE', {args});\n"
        "  ELSE\n"
        f"    EXECUTE format('GRANT %I TO %I', {args});\n"
        "  END IF;"
    )


def _grant_owner_to_admin(owner: str) -> str:
    """Make a non-superuser admin a member of the owner keyring.

    The admin needs owner rights for default privileges, grants on existing
    objects and rollback. A superuser has them already.
    """
    return _do(
        "  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = session_user) THEN\n"
        "    IF pg_catalog.current_setting('server_version_num')::int >= 160000 THEN\n"
        "      EXECUTE format('GRANT %I TO %I WITH INHERIT TRUE, SET TRUE', "
        f"{quote_literal(owner)}, session_user);\n"
        "    ELSE\n"
        f"      EXECUTE format('GRANT %I TO %I', {quote_literal(owner)}, session_user);\n"
        "    END IF;\n"
        "  END IF;"
    )


def _create_extension(name: str) -> str:
    """Create an extension as the admin so migrations never need superuser.

    Mirrors the migrations' IBM-aware form. A failure only warns: the
    migration that needs the extension fails with its own hint.
    """
    ext = quote_ident(name)
    return _do(
        f"  IF EXISTS (SELECT FROM pg_catalog.pg_extension WHERE extname = {quote_literal(name)}) THEN\n"
        "    NULL;\n"
        "  ELSIF pg_catalog.to_regnamespace('ibm_extension') IS NOT NULL THEN\n"
        f"    CREATE EXTENSION IF NOT EXISTS {ext};\n"
        "  ELSE\n"
        f"    CREATE EXTENSION IF NOT EXISTS {ext} WITH SCHEMA public;\n"
        "  END IF;\n"
        "EXCEPTION WHEN OTHERS THEN\n"
        f"  RAISE WARNING 'Could not create extension %: %', {quote_literal(name)}, SQLERRM;"
    )


def _hide_readonly_columns(readonly: str) -> str:
    """Swap table-level SELECT for every column but the hidden ones."""
    rows = ",\n".join(
        f"      ({quote_literal(table)}, ARRAY[{', '.join(quote_literal(c) for c in cols)}]::text[])"
        for table, cols in READONLY_HIDDEN_COLUMNS
    )
    ro = quote_literal(readonly)
    return (
        "DO $bootstrap$\n"
        "DECLARE\n  t text;\n  hidden text[];\n  rel regclass;\n  keep text;\n"
        "BEGIN\n"
        f"  FOR t, hidden IN SELECT * FROM (VALUES\n{rows}\n    ) AS v(t, hidden) LOOP\n"
        "    rel := pg_catalog.to_regclass(format('public.%I', t));\n"
        "    CONTINUE WHEN rel IS NULL;\n"
        f"    EXECUTE format('REVOKE SELECT ON TABLE %s FROM %I', rel, {ro});\n"
        "    SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum) INTO keep\n"
        "    FROM pg_catalog.pg_attribute\n"
        "    WHERE attrelid = rel AND attnum > 0 AND NOT attisdropped AND NOT (attname = ANY (hidden));\n"
        "    IF keep IS NOT NULL THEN\n"
        f"      EXECUTE format('GRANT SELECT (%s) ON TABLE %s TO %I', keep, rel, {ro});\n"
        "    END IF;\n"
        "  END LOOP;\n"
        "END\n$bootstrap$;"
    )


def _verify(login: LoginMethod, roles: RoleNames) -> str:
    """Fail the bootstrap if the topology's safety rules don't hold.

    Raising rolls back the whole transaction, so a failed check changes nothing.
    """
    managed = ", ".join(quote_literal(r) for r in (*roles.keyrings, *roles.logins))
    keyrings = ", ".join(quote_literal(r) for r in roles.keyrings)
    logins = ", ".join(quote_literal(r) for r in roles.logins)
    iam = quote_literal(roles.iam)
    # MEMBER counts every grant, including the ADMIN-only grant PG 16 gives a
    # CREATEROLE admin on roles it creates. Strict on purpose: which grants RDS
    # treats as IAM-only isn't documented, and a lockout is worse than a refusal.
    # On RDS 16.13 the master creating IAM login roles was verified not to be a
    # MEMBER of rds_iam (staging, 2026-11-06), so this does not block RDS; the
    # IAM role exists nowhere else.
    in_iam = f"pg_catalog.pg_has_role(oid, {iam}, 'MEMBER')"
    # A superuser is a member of every role; RDS has no superuser login.
    must_not_be_in_iam = (
        f"rolname IN ({keyrings}) OR (rolname = session_user AND NOT rolsuper)"
    )
    if login is LoginMethod.PASSWORD:
        must_not_be_in_iam += f" OR rolname IN ({logins})"
    return _do(
        "  IF EXISTS (SELECT FROM pg_catalog.pg_roles\n"
        f"             WHERE rolname IN ({managed})\n"
        "               AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolreplication)) THEN\n"
        # CREATEROLE can grant itself the owner keyring; REPLICATION reads the WAL.
        "    RAISE EXCEPTION 'A Tracecat role is SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION';\n"
        "  END IF;\n"
        "  IF EXISTS (SELECT FROM pg_catalog.pg_roles\n"
        f"             WHERE rolname IN ({keyrings}) AND rolcanlogin) THEN\n"
        "    RAISE EXCEPTION 'A Tracecat keyring role can log in';\n"
        "  END IF;\n"
        "  IF EXISTS (\n"
        # pg_shdepend records every owner and ACL dependency on a role, in every
        # database; dbid 0 holds shared objects such as databases themselves.
        # A dependency on any role a Tracecat role is a member of counts too
        # (inherited access), except the IAM role, which RDS grants itself.
        "    SELECT FROM pg_catalog.pg_shdepend s\n"
        "    WHERE s.refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass\n"
        f"      AND s.refobjid IS DISTINCT FROM pg_catalog.to_regrole({iam})::oid\n"
        "      AND EXISTS (SELECT FROM pg_catalog.pg_roles r\n"
        f"                  WHERE r.rolname IN ({managed})\n"
        "                    AND pg_catalog.pg_has_role(r.oid, s.refobjid, 'MEMBER'))\n"
        "      AND s.dbid <> (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database())\n"
        "      AND NOT (s.classid = 'pg_catalog.pg_database'::pg_catalog.regclass\n"
        "               AND s.objid = (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database()))\n"
        "  ) THEN\n"
        "    RAISE EXCEPTION 'A Tracecat role already has privileges on another database'\n"
        "      USING HINT = 'Roles are cluster-wide: run one Tracecat install per cluster, '\n"
        "        'and check that TRACECAT__DB_ADMIN_URI names the Tracecat database.';\n"
        "  END IF;\n"
        f"  IF pg_catalog.to_regrole({iam}) IS NOT NULL AND EXISTS (\n"
        f"    SELECT FROM pg_catalog.pg_roles WHERE ({must_not_be_in_iam}) AND {in_iam}\n"
        "  ) THEN\n"
        f"    RAISE EXCEPTION 'A role that needs password login would be a member of %', {iam}\n"
        "      USING DETAIL = 'The admin, a keyring, or (with --login password) a login role. Nothing was changed.',\n"
        "            HINT = 'On PostgreSQL 16 a CREATEROLE admin is an ADMIN-only member of the roles it '\n"
        "              'creates, which counts here; RDS does not document whether that disables '\n"
        "              'password login, so bootstrap refuses rather than risk it.';\n"
        "  END IF;"
    )


def bootstrap_statements(
    login: LoginMethod, roles: RoleNames = DEFAULT_ROLES
) -> list[str]:
    """Build the idempotent bootstrap SQL, in order.

    Passwords are not part of it; :func:`run_bootstrap` sets them separately.

    Args:
        login: How the login roles authenticate.
        roles: Role names.

    Returns:
        SQL statements to run in one transaction as the admin.
    """
    q = quote_ident
    owner, dyn, ro = q(roles.owner), q(roles.dynamic_owner), q(roles.readonly)
    migrator, app, runtime = q(roles.migrator), q(roles.app), q(roles.agent_runtime)

    statements: list[str] = []
    for keyring in roles.keyrings:
        statements += _create_role(keyring, login=False)
    for name in roles.logins:
        if login is LoginMethod.AZURE_ENTRA:
            statements.append(_require_role(name))
            statements.append(f"ALTER ROLE {q(name)} LOGIN;")
        else:
            statements += _create_role(name, login=True)
    if login is LoginMethod.AWS_IAM:
        statements.append(f"GRANT {q(roles.iam)} TO {migrator}, {app}, {runtime};")

    statements += [
        _grant_membership(roles.dynamic_owner, roles.owner),
        _grant_membership(roles.owner, roles.migrator),
        _grant_membership(roles.dynamic_owner, roles.app),
        _grant_membership(roles.agent_runtime, roles.app),
        _grant_owner_to_admin(roles.owner),
        # New objects created by migrations belong to the owner keyring.
        f"ALTER ROLE {migrator} SET role = {quote_literal(roles.owner)};",
    ]

    statements += [
        _do(
            "  EXECUTE format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I, %I, %I, %I',\n"
            f"    current_database(), {quote_literal(roles.migrator)}, {quote_literal(roles.app)},\n"
            f"    {quote_literal(roles.agent_runtime)}, {quote_literal(roles.readonly)});\n"
            # The app creates workspace schemas, then hands them to app_owner.
            "  EXECUTE format('GRANT CREATE ON DATABASE %I TO %I, %I, %I',\n"
            f"    current_database(), {quote_literal(roles.owner)},\n"
            f"    {quote_literal(roles.dynamic_owner)}, {quote_literal(roles.app)});"
        ),
        f"GRANT USAGE, CREATE ON SCHEMA public TO {owner};",
        f"GRANT USAGE ON SCHEMA public TO {dyn}, {app}, {runtime}, {ro};",
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC;",
        _create_extension("vector"),
        _create_extension("pg_trgm"),
    ]

    # Objects that already exist (reruns, upgrades). On a fresh database
    # these are no-ops and the default privileges below cover new objects.
    statements += [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app};",
        f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {app};",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ro};",
        _hide_readonly_columns(roles.readonly),
        # custom_fields_*.case_fields has an FK to case; app inherits this.
        _do(
            "  IF pg_catalog.to_regclass('public.\"case\"') IS NOT NULL THEN\n"
            f"    EXECUTE format('GRANT REFERENCES ON TABLE public.%I TO %I', 'case', "
            f"{quote_literal(roles.dynamic_owner)});\n"
            "  END IF;"
        ),
    ]

    default = f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public GRANT"
    statements += [
        f"{default} SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app};",
        f"{default} USAGE, SELECT, UPDATE ON SEQUENCES TO {app};",
        _verify(login, roles),
    ]
    return statements


@dataclass(frozen=True, slots=True)
class LoginPasswords:
    """Passwords for the login roles; ``None`` leaves a password unchanged."""

    migrator: str | None = None
    app: str | None = None
    agent_runtime: str | None = None

    @classmethod
    def from_config(cls) -> LoginPasswords:
        return cls(
            migrator=config.TRACECAT__DB_MIGRATOR_PASSWORD,
            app=config.TRACECAT__DB_APP_PASSWORD,
            agent_runtime=config.TRACECAT__DB_AGENT_RUNTIME_PASSWORD,
        )

    def by_role(self, roles: RoleNames) -> list[tuple[str, str | None]]:
        return [
            (roles.migrator, self.migrator),
            (roles.app, self.app),
            (roles.agent_runtime, self.agent_runtime),
        ]


def run_bootstrap(
    conn: psycopg.Connection,
    login: LoginMethod,
    passwords: LoginPasswords,
    roles: RoleNames = DEFAULT_ROLES,
) -> list[str]:
    """Apply the bootstrap in one transaction.

    Passwords are hashed with SCRAM on the client, so the plaintext never
    reaches the server. The verifier does, and a server logging all statements
    records it; verifiers are crackable offline, so treat such logs as secret.

    Args:
        conn: Admin connection, not in a transaction.
        login: How the login roles authenticate.
        passwords: Passwords to set when ``login`` is ``password``.
        roles: Role names.

    Returns:
        Notes for the operator.

    Raises:
        ValueError: Passwords were given for a non-password login method.
    """
    given = [name for name, pw in passwords.by_role(roles) if pw]
    if given and login is not LoginMethod.PASSWORD:
        raise ValueError(
            f"Passwords are only used with --login password, got some for {given}"
        )
    notes: list[str] = []
    with conn.transaction(), conn.cursor() as cur:
        for statement in bootstrap_statements(login, roles):
            # Built from quoted identifiers and literals, never from input.
            cur.execute(statement.encode())
        if login is LoginMethod.PASSWORD:
            for name, password in passwords.by_role(roles):
                if not password:
                    notes.append(f"{name}: password unchanged")
                    continue
                hashed = conn.pgconn.encrypt_password(
                    password.encode(), name.encode(), b"scram-sha-256"
                )
                alter = f"ALTER ROLE {quote_ident(name)} PASSWORD {quote_literal(hashed.decode())}"
                cur.execute(alter.encode())
                notes.append(f"{name}: password set")
    return notes


def render_sql(login: LoginMethod, roles: RoleNames = DEFAULT_ROLES) -> str:
    """Render the bootstrap as a script for an admin to run by hand."""
    lines = [
        "-- Tracecat role bootstrap. Run once as an admin, in the Tracecat database.",
        "BEGIN;",
        *bootstrap_statements(login, roles),
        "COMMIT;",
    ]
    if login is LoginMethod.PASSWORD:
        lines.append(
            "-- Then set passwords with psql's \\password, which hashes them on the client:"
        )
        lines += [f"--   \\password {quote_ident(name)}" for name in roles.logins]
    lines.append(
        f"-- Then set {DYNAMIC_OWNER_ENV_VAR}={roles.dynamic_owner} on the app."
    )
    return "\n".join(lines) + "\n"


def _admin_conninfo() -> str:
    if not (uri := config.TRACECAT__DB_ADMIN_URI):
        raise SystemExit("Set TRACECAT__DB_ADMIN_URI to an admin connection URI")
    # psycopg takes a libpq URI, without SQLAlchemy's +driver suffix.
    url = make_url(uri).set(drivername="postgresql")
    if "sslmode" not in url.query:
        # libpq defaults to sslmode=prefer, which silently falls back to plaintext.
        url = url.update_query_dict({"sslmode": config.TRACECAT__DB_SSLMODE})
    return url.render_as_string(hide_password=False)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m tracecat.db.roles``."""
    parser = argparse.ArgumentParser(prog="python -m tracecat.db.roles")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap", help="Create or repair the Tracecat roles and grants."
    )
    bootstrap.add_argument(
        "--login",
        type=LoginMethod,
        choices=list(LoginMethod),
        default=LoginMethod.PASSWORD,
        help="How the login roles authenticate (default: password).",
    )
    bootstrap.add_argument(
        "--sql",
        action="store_true",
        help="Print the SQL instead of running it (no passwords included).",
    )
    args = parser.parse_args(argv)

    login: LoginMethod = args.login
    if args.sql:
        sys.stdout.write(render_sql(login))
        return 0

    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        notes = run_bootstrap(conn, login, LoginPasswords.from_config())
    for note in notes:
        print(note, file=sys.stderr)
    print(f"{DYNAMIC_OWNER_ENV_VAR}={DEFAULT_ROLES.dynamic_owner}")
    return 0


LOGIN_CHECK_SQL = text(
    """
    SELECT r.rolname, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolreplication,
           EXISTS (
             SELECT FROM pg_catalog.pg_class c
             JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
               AND pg_catalog.pg_has_role(r.oid, c.relowner, 'MEMBER')
           ) AS owns_public_table
    FROM pg_catalog.pg_roles r
    -- The login, the role it is set to, and every role it can SET ROLE to
    -- or inherits from.
    WHERE r.rolname IN (session_user, current_user)
       OR pg_catalog.pg_has_role(session_user, r.oid, 'MEMBER')
    ORDER BY r.rolname
    """
)


async def assert_login_can_enforce_rls(conn: AsyncConnection) -> None:
    """Refuse a login that is privileged or a ``public`` table owner.

    Postgres never applies RLS to a superuser or a ``BYPASSRLS`` role, and a
    table owner (or a member of the owner role) can turn RLS off. This is a
    necessary condition for enforce mode, not proof of isolation: dynamic
    tables are owned by ``app_owner`` on purpose and rely on FORCE RLS plus
    the DDL guard, and policies still honour ``app.rls_bypass``.

    Args:
        conn: A connection logged in as the app's database user.

    Raises:
        UnsafeDatabaseLoginError: The login, or any role it is a member of,
            is a superuser, has ``BYPASSRLS``, ``CREATEROLE`` or ``REPLICATION``,
            or can act as a ``public`` owner.
    """
    problems: list[str] = []
    rows = (await conn.execute(LOGIN_CHECK_SQL)).tuples()
    for name, is_super, bypass, createrole, replication, owns in rows:
        if is_super:
            problems.append(f"{name} is a superuser")
        if bypass:
            problems.append(f"{name} has BYPASSRLS")
        if createrole:
            # It can grant itself the owner role and then turn RLS off.
            problems.append(f"{name} has CREATEROLE")
        if replication:
            problems.append(f"{name} has REPLICATION")
        if owns:
            problems.append(
                f"{name} owns tables in public (or is a member of their owner)"
            )
    if problems:
        raise UnsafeDatabaseLoginError(
            "TRACECAT__RLS_MODE=enforce needs a login without SUPERUSER, BYPASSRLS, "
            "CREATEROLE or REPLICATION that owns no public table: "
            + "; ".join(problems)
            + ". Log in as the 'app' role (python -m tracecat.db.roles bootstrap)."
        )


if __name__ == "__main__":
    raise SystemExit(main())
