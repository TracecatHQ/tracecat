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

``upgrade`` moves an existing install onto the topology: it runs the bootstrap,
then hands ``public`` objects owned by the old login (``--from``, default the
admin) to ``tracecat_owner`` and dynamic schemas to ``app_owner``, one object
or schema per transaction with a short lock timeout, so a busy install keeps
running. It never grants the admin membership in a login role.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
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
    """Wrap a PL/pgSQL body in a DO block.

    The dollar-quote tag is picked so it can't occur in the body: some bodies
    carry catalog-derived names, and a name containing the tag would end the
    block early.
    """
    tag = "bootstrap"
    while f"${tag}$" in body:
        tag += "_"
    return f"DO ${tag}$\nBEGIN\n{body}\nEND\n${tag}$;"


def _create_role(name: str, *, login: bool) -> list[str]:
    kind = "LOGIN" if login else "NOLOGIN"
    create = _do(
        f"  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = {quote_literal(name)}) THEN\n"
        f"    CREATE ROLE {quote_ident(name)} {kind};\n"
        "  END IF;"
    )
    # INHERIT: before PG 16 a member inherits only if the member role has it.
    return [create, f"ALTER ROLE {quote_ident(name)} {kind} INHERIT;"]


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
    take inheritance from the member role, which bootstrap sets to INHERIT.
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
        # IBM's provider-owned schema only: any role with CREATE on the database
        # could create an ibm_extension schema elsewhere and plant objects in it.
        "  ELSIF EXISTS (SELECT FROM pg_catalog.pg_namespace n\n"
        "              JOIN pg_catalog.pg_roles r ON r.oid = n.nspowner\n"
        "              WHERE n.nspname = 'ibm_extension' AND r.rolsuper) THEN\n"
        # IBM places the extension through the path; restore the pin after.
        "    PERFORM pg_catalog.set_config('search_path', 'ibm_extension, pg_catalog, pg_temp', true);\n"
        f"    CREATE EXTENSION IF NOT EXISTS {ext};\n"
        "    PERFORM pg_catalog.set_config('search_path', 'pg_catalog, pg_temp', true);\n"
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
        # Always public.<table>: regclass text drops the schema when visible.
        f"    EXECUTE format('REVOKE SELECT ON TABLE public.%I FROM %I', t, {ro});\n"
        "    SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum) INTO keep\n"
        "    FROM pg_catalog.pg_attribute\n"
        "    WHERE attrelid = rel AND attnum > 0 AND NOT attisdropped AND NOT (attname = ANY (hidden));\n"
        "    IF keep IS NOT NULL THEN\n"
        f"      EXECUTE format('GRANT SELECT (%s) ON TABLE public.%I TO %I', keep, t, {ro});\n"
        "    END IF;\n"
        "  END LOOP;\n"
        "END\n$bootstrap$;"
    )


def _owner_reach_body(roles: RoleNames) -> str:
    """PL/pgSQL check: only migrator and admins may act as the owner keyring.

    A pre-created ``tracecat_owner`` holding ``app``, or an old-owner group
    that contains a topology role and is given the keyring to keep working,
    would put that role above RLS on ``public``.
    """
    names = (roles.app, roles.agent_runtime, roles.readonly, roles.dynamic_owner)
    protected = ", ".join(quote_literal(r) for r in names)
    # Only app and the owner keyring may hold the dynamic keyring. migrator
    # reaches it through the owner keyring by design, so it's checked for a
    # direct grant only; readonly and agent_runtime must not reach it at all.
    never_dynamic = ", ".join(
        quote_literal(r) for r in (roles.agent_runtime, roles.readonly)
    )
    dyn = quote_literal(roles.dynamic_owner)
    owner = quote_literal(roles.owner)
    return (
        # The database owner owns public (PG 15+), as does an explicit owner of
        # the public schema; either can drop it with everything in it.
        "  IF EXISTS (SELECT FROM pg_catalog.pg_roles r, pg_catalog.pg_database d,\n"
        "                         pg_catalog.pg_namespace n\n"
        "             WHERE d.datname = pg_catalog.current_database() AND n.nspname = 'public'\n"
        f"               AND r.rolname IN ({protected})\n"
        "               AND (pg_catalog.pg_has_role(r.oid, d.datdba, 'MEMBER')\n"
        "                    OR pg_catalog.pg_has_role(r.oid, n.nspowner, 'MEMBER'))) THEN\n"
        "    RAISE EXCEPTION 'A Tracecat role can act as the owner of the database or of public, "
        "and could drop it'\n"
        "      USING HINT = 'Give the database and public to the admin (ALTER DATABASE / ALTER SCHEMA "
        "public OWNER TO), then rerun bootstrap.';\n"
        "  END IF;\n"
        "  IF EXISTS (SELECT FROM pg_catalog.pg_roles\n"
        f"             WHERE rolname IN ({protected})\n"
        f"               AND pg_catalog.pg_has_role(oid, {owner}, 'MEMBER')) THEN\n"
        f"    RAISE EXCEPTION '% can act as the owner keyring %', "
        f"{quote_literal(', '.join(names))}, {owner}\n"
        "      USING HINT = 'Revoke that membership; only migrator and admins may hold it.';\n"
        "  END IF;\n"
        "  IF EXISTS (SELECT FROM pg_catalog.pg_roles\n"
        f"             WHERE rolname IN ({never_dynamic})\n"
        f"               AND pg_catalog.pg_has_role(oid, {dyn}, 'MEMBER'))\n"
        "     OR EXISTS (SELECT FROM pg_catalog.pg_auth_members m\n"
        "             JOIN pg_catalog.pg_roles u ON u.oid = m.member\n"
        f"             WHERE m.roleid = {dyn}::pg_catalog.regrole\n"
        f"               AND u.rolname = {quote_literal(roles.migrator)}) THEN\n"
        f"    RAISE EXCEPTION 'A topology role other than % holds the dynamic keyring %', "
        f"{quote_literal(roles.app)}, {quote_literal(roles.dynamic_owner)}\n"
        "      USING HINT = 'Revoke that membership; it would own every dynamic table.';\n"
        "  END IF;\n"
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
        "  END IF;\n" + _owner_reach_body(roles) + "  IF EXISTS (\n"
        # Only app may be a member of a login role (of agent_runtime). Anything
        # else would inherit that login's keyring, e.g. a pre-created migrator.
        # PG 16's ADMIN-only creator grant is allowed only for roles that can
        # already act as the owner keyring (the admin, an earlier admin): an
        # ADMIN holder can grant itself SET, which then adds nothing for them.
        "    SELECT FROM pg_catalog.pg_auth_members m\n"
        "    JOIN pg_catalog.pg_roles g ON g.oid = m.roleid\n"
        "    JOIN pg_catalog.pg_roles u ON u.oid = m.member\n"
        f"    WHERE g.rolname IN ({logins})\n"
        f"      AND NOT (g.rolname = {quote_literal(roles.agent_runtime)}"
        f" AND u.rolname = {quote_literal(roles.app)})\n"
        # CASE, not AND: the 'SET' privilege type only exists from PG 16.
        "      AND NOT CASE WHEN pg_catalog.current_setting('server_version_num')::int < 160000\n"
        "                   THEN false\n"
        "               ELSE m.admin_option\n"
        "                    AND NOT pg_catalog.pg_has_role(u.oid, g.oid, 'USAGE')\n"
        "                    AND NOT pg_catalog.pg_has_role(u.oid, g.oid, 'SET')\n"
        # USAGE, not MEMBER: MEMBER would count this very grant (via migrator).
        "                    AND (u.rolname = session_user\n"
        f"                         OR pg_catalog.pg_has_role(u.oid, {quote_literal(roles.owner)}, 'USAGE'))\n"
        "           END\n"
        "  ) THEN\n"
        "    RAISE EXCEPTION 'A Tracecat login role has unexpected members'\n"
        "      USING HINT = 'Revoke them (they would inherit or could take its access), "
        "then rerun bootstrap as the admin that created the role.';\n"
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

    # Resolve every function and operator from pg_catalog: a database owner
    # can set a database-wide search_path and plant public.format() or an
    # operator that would run as the admin. Everything in public is qualified.
    # standard_conforming_strings too: quote_literal only doubles quotes, so a
    # database-level "off" would let a crafted role name escape a literal.
    statements: list[str] = [
        "SET LOCAL search_path = pg_catalog, pg_temp;",
        "SET LOCAL standard_conforming_strings = on;",
    ]
    for keyring in roles.keyrings:
        statements += _create_role(keyring, login=False)
    for name in roles.logins:
        if login is LoginMethod.AZURE_ENTRA:
            statements.append(_require_role(name))
            statements.append(f"ALTER ROLE {q(name)} LOGIN INHERIT;")
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
    *,
    lock_timeout: str | None = None,
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
        lock_timeout: Postgres ``lock_timeout`` for the transaction; ``None``
            waits as long as the server's setting allows.

    Returns:
        Notes for the operator.

    Raises:
        ValueError: Passwords were given for a non-password login method.
        psycopg.errors.LockNotAvailable: A lock wasn't granted within
            ``lock_timeout``; nothing was changed.
    """
    given = [name for name, pw in passwords.by_role(roles) if pw]
    if given and login is not LoginMethod.PASSWORD:
        raise ValueError(
            f"Passwords are only used with --login password, got some for {given}"
        )
    notes: list[str] = []
    with conn.transaction(), conn.cursor() as cur:
        # Before any client-built literal is parsed (see bootstrap_statements).
        cur.execute(b"SET LOCAL standard_conforming_strings = on")
        if lock_timeout is not None:
            timeout = f"SET LOCAL lock_timeout = {quote_literal(lock_timeout)}"
            cur.execute(timeout.encode())
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


def _printable(text: str) -> str:
    """Escape control characters: catalog names can carry terminal escapes."""
    return "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in text)


def _admin_conninfo() -> str:
    if not (uri := config.TRACECAT__DB_ADMIN_URI):
        raise SystemExit("Set TRACECAT__DB_ADMIN_URI to an admin connection URI")
    # psycopg takes a libpq URI, without SQLAlchemy's +driver suffix.
    url = make_url(uri).set(drivername="postgresql")
    if "sslmode" not in url.query:
        # libpq defaults to sslmode=prefer, which silently falls back to plaintext.
        url = url.update_query_dict({"sslmode": config.TRACECAT__DB_SSLMODE})
    return url.render_as_string(hide_password=False)


# Objects in public that belong to the old owner and can be re-owned one by
# one. Extension members stay with the extension; sequences owned by a column
# and table row types move with their table.
PUBLIC_OBJECTS_TEMPLATE = """
SELECT CASE WHEN c.relkind IN ('v', 'm') THEN 2 ELSE 1 END,
       CASE c.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
                         WHEN 'S' THEN 'SEQUENCE' WHEN 'f' THEN 'FOREIGN TABLE'
                         ELSE 'TABLE' END,
       c.oid::pg_catalog.regclass::text, pg_catalog.pg_get_userbyid(c.relowner)
FROM pg_catalog.pg_class c
WHERE c.relnamespace = 'public'::pg_catalog.regnamespace
  AND OWNER_FILTER_C
  AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
  AND NOT EXISTS (SELECT FROM pg_catalog.pg_depend d
                  WHERE d.classid = 'pg_catalog.pg_class'::pg_catalog.regclass
                    AND d.objid = c.oid AND d.deptype = 'e')
  AND NOT (c.relkind = 'S' AND EXISTS (
        SELECT FROM pg_catalog.pg_depend d
        WHERE d.classid = 'pg_catalog.pg_class'::pg_catalog.regclass AND d.objid = c.oid
          AND d.refclassid = 'pg_catalog.pg_class'::pg_catalog.regclass
          AND d.deptype IN ('a', 'i')))
UNION ALL
SELECT 3, 'ROUTINE', p.oid::pg_catalog.regprocedure::text,
       pg_catalog.pg_get_userbyid(p.proowner)
FROM pg_catalog.pg_proc p
WHERE p.pronamespace = 'public'::pg_catalog.regnamespace
  AND OWNER_FILTER_P
  AND NOT EXISTS (SELECT FROM pg_catalog.pg_depend d
                  WHERE d.classid = 'pg_catalog.pg_proc'::pg_catalog.regclass
                    AND d.objid = p.oid AND d.deptype = 'e')
UNION ALL
SELECT 3, CASE t.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END,
       t.oid::pg_catalog.regtype::text, pg_catalog.pg_get_userbyid(t.typowner)
FROM pg_catalog.pg_type t
WHERE t.typnamespace = 'public'::pg_catalog.regnamespace
  AND OWNER_FILTER_T
  AND t.typtype IN ('e', 'd', 'r', 'c')
  AND (t.typrelid = 0
       OR (SELECT c.relkind FROM pg_catalog.pg_class c WHERE c.oid = t.typrelid) = 'c')
  AND NOT EXISTS (SELECT FROM pg_catalog.pg_depend d
                  WHERE d.classid = 'pg_catalog.pg_type'::pg_catalog.regclass
                    AND d.objid = t.oid AND d.deptype = 'e')
ORDER BY 1, 2, 3
"""


def _public_objects_sql(relations: str, others: str | None = None) -> str:
    """Fill the owner filters: one for relations, one for routines and types."""
    others = relations if others is None else others
    return (
        PUBLIC_OBJECTS_TEMPLATE.replace(
            "OWNER_FILTER_C", relations.format("c.relowner")
        )
        .replace("OWNER_FILTER_P", others.format("p.proowner"))
        .replace("OWNER_FILTER_T", others.format("t.typowner"))
    )


# Objects the old owners still own: what to move.
PUBLIC_OBJECTS_SQL = _public_objects_sql(
    "pg_catalog.pg_get_userbyid({}) = ANY(%(old)s)"
)

# Objects not on the owner keyring: what makes an upgrade incomplete. Every
# relation counts. A superuser's routines and types count only when it was
# named as an old owner, so RDS's rdsadmin functions don't fail every run.
_NOT_OWNER = "{0} <> %(owner)s::pg_catalog.regrole"
_SUPERUSER_UNNAMED = (
    "NOT pg_catalog.pg_get_userbyid({0}) = ANY(%(old)s) "
    "AND (SELECT rolsuper FROM pg_catalog.pg_roles WHERE oid = {0})"
)
UNMOVED_PUBLIC_SQL = _public_objects_sql(
    _NOT_OWNER, f"{_NOT_OWNER} AND NOT ({_SUPERUSER_UNNAMED})"
)

SUPERUSER_OWNED_PUBLIC_SQL = (
    "SELECT owner, count(*) FROM ("
    + _public_objects_sql("false", f"{_NOT_OWNER} AND {_SUPERUSER_UNNAMED}").replace(
        "ORDER BY 1, 2, 3", ""
    )
    + ") AS o(stage, kind, name, owner) GROUP BY owner ORDER BY owner"
)

# Dynamic schemas that still need a new owner or FORCE RLS on a table.
DYNAMIC_SCHEMAS_SQL = """
SELECT n.nspname
FROM pg_catalog.pg_namespace n
WHERE (n.nspname LIKE 'tables\\_%%' OR n.nspname LIKE 'custom\\_fields\\_%%')
  AND (pg_catalog.pg_get_userbyid(n.nspowner) <> %(dyn)s OR EXISTS (
        SELECT FROM pg_catalog.pg_class c
        WHERE c.relnamespace = n.oid AND c.relkind IN ('r', 'p')
          AND (pg_catalog.pg_get_userbyid(c.relowner) <> %(dyn)s
               OR (c.relrowsecurity AND NOT c.relforcerowsecurity)
               OR (NOT c.relrowsecurity AND EXISTS (
                     SELECT FROM pg_catalog.pg_policy pol WHERE pol.polrelid = c.oid)))))
ORDER BY 1
"""

DYNAMIC_TABLES_SQL = """
SELECT c.oid::pg_catalog.regclass::text,
       pg_catalog.pg_get_userbyid(c.relowner) <> %(dyn)s,
       NOT c.relforcerowsecurity
         AND (c.relrowsecurity
              OR EXISTS (SELECT FROM pg_catalog.pg_policy pol WHERE pol.polrelid = c.oid)),
       NOT c.relrowsecurity
         AND EXISTS (SELECT FROM pg_catalog.pg_policy pol WHERE pol.polrelid = c.oid)
FROM pg_catalog.pg_class c
WHERE c.relnamespace = %(schema)s::pg_catalog.regnamespace AND c.relkind IN ('r', 'p')
ORDER BY 1
"""

# Same rule as the bootstrap's final check, run after every transaction.
IAM_LOCKOUT_SQL = """
SELECT CASE WHEN pg_catalog.to_regrole(%(iam)s) IS NULL THEN false
            ELSE EXISTS (SELECT FROM pg_catalog.pg_roles
                         WHERE (rolname = ANY(%(guarded)s)
                                OR (rolname = session_user AND NOT rolsuper))
                           AND pg_catalog.pg_has_role(oid, %(iam)s, 'MEMBER'))
       END
"""


class UpgradeAbortedError(RuntimeError):
    """The admin or a keyring became a member of the IAM role mid-upgrade."""


@dataclass(slots=True)
class UpgradeResult:
    """What an upgrade run moved, what it couldn't, and what is left."""

    notes: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    kept_access: list[tuple[str, str]] = field(default_factory=list)
    """(keyring, role) grants an old owner keeps until cutover."""
    failed: list[tuple[str, str]] = field(default_factory=list)
    remaining_public: list[str] = field(default_factory=list)
    remaining_dynamic: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not (self.failed or self.remaining_public or self.remaining_dynamic)


@dataclass(frozen=True, slots=True)
class PublicObject:
    stage: int  # 1: tables, sequences; 2: views; 3: routines, types. Each waits for the last.
    kind: str
    name: str  # schema-qualified: the upgrade runs with search_path = pg_catalog
    owner: str

    @property
    def label(self) -> str:
        return f"{self.kind} {self.name}"


def _public_objects(conn: psycopg.Connection, old: Sequence[str]) -> list[PublicObject]:
    rows = conn.execute(PUBLIC_OBJECTS_SQL.encode(), {"old": list(old)}).fetchall()
    return [PublicObject(*row) for row in rows]


def _unmoved_public(
    conn: psycopg.Connection, old: Sequence[str], roles: RoleNames
) -> list[PublicObject]:
    rows = conn.execute(
        UNMOVED_PUBLIC_SQL.encode(),
        {"old": list(old), "owner": quote_ident(roles.owner)},
    ).fetchall()
    return [PublicObject(*row) for row in rows]


def _dynamic_schemas(conn: psycopg.Connection, roles: RoleNames) -> list[str]:
    rows = conn.execute(DYNAMIC_SCHEMAS_SQL, {"dyn": roles.dynamic_owner}).fetchall()
    return [name for (name,) in rows]


def _dynamic_statements(
    conn: psycopg.Connection, schema: str, roles: RoleNames
) -> list[str]:
    """ALTER statements that re-own one dynamic schema and force RLS on it."""
    dyn = quote_ident(roles.dynamic_owner)
    owner = conn.execute(
        "SELECT pg_catalog.pg_get_userbyid(nspowner) FROM pg_catalog.pg_namespace "
        "WHERE nspname = %(schema)s",
        {"schema": schema},
    ).fetchone()
    statements: list[str] = []
    if owner and owner[0] != roles.dynamic_owner:
        statements.append(f"ALTER SCHEMA {quote_ident(schema)} OWNER TO {dyn}")
    tables = conn.execute(
        DYNAMIC_TABLES_SQL,
        {"dyn": roles.dynamic_owner, "schema": quote_ident(schema)},
    ).fetchall()
    for table, needs_owner, needs_force, needs_enable in tables:
        if needs_owner:
            # Moves the table's indexes and owned sequences too.
            statements.append(f"ALTER TABLE {table} OWNER TO {dyn}")
        if needs_enable:
            # A policy with RLS switched off protects nothing (same repair as
            # the dynamic-RLS guard migration's backfill).
            statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        if needs_force:
            statements.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    return statements


def _assert_no_iam_lockout(
    conn: psycopg.Connection, login: LoginMethod, roles: RoleNames
) -> None:
    guarded = list(roles.keyrings)
    if login is LoginMethod.PASSWORD:
        guarded += roles.logins
    row = conn.execute(
        IAM_LOCKOUT_SQL, {"iam": roles.iam, "guarded": guarded}
    ).fetchone()
    if row and row[0]:
        raise UpgradeAbortedError(
            f"A role that needs password login is now a member of {roles.iam}; "
            "stopping before anything else changes. Revoke that membership."
        )


def _apply(
    conn: psycopg.Connection,
    label: str,
    statements: Sequence[str],
    *,
    lock_timeout: str,
    retries: int,
    result: UpgradeResult,
    record: bool = True,
) -> bool:
    """Run one object's statements in their own short transaction.

    Returns whether they committed; failures are added to ``result.failed``,
    successes to ``result.moved`` when ``record`` is set.
    """
    for attempt in range(retries + 1):
        try:
            with conn.transaction():
                # Before any quoted literal is parsed (see bootstrap_statements).
                conn.execute(b"SET LOCAL standard_conforming_strings = on")
                timeout = f"SET LOCAL lock_timeout = {quote_literal(lock_timeout)}"
                conn.execute(timeout.encode())
                for statement in statements:
                    # Names come from regclass/regprocedure/regtype casts or quote_ident.
                    conn.execute(statement.encode())
        except psycopg.errors.LockNotAvailable:
            if attempt < retries:
                time.sleep(1)
                continue
            result.failed.append((label, "lock timeout; rerun upgrade later"))
            return False
        except psycopg.Error as exc:
            result.failed.append((label, str(exc).strip()))
            return False
        if record:
            result.moved.append(label)
        return True
    return False


def _bootstrap_with_retries(
    conn: psycopg.Connection,
    login: LoginMethod,
    passwords: LoginPasswords,
    roles: RoleNames,
    *,
    lock_timeout: str,
    retries: int,
) -> list[str]:
    for attempt in range(retries + 1):
        try:
            return run_bootstrap(
                conn, login, passwords, roles, lock_timeout=lock_timeout
            )
        except psycopg.errors.LockNotAvailable:
            if attempt == retries:
                raise
            time.sleep(1)
    raise AssertionError("unreachable")


def run_upgrade(
    conn: psycopg.Connection,
    login: LoginMethod,
    passwords: LoginPasswords,
    roles: RoleNames = DEFAULT_ROLES,
    old_owners: Sequence[str] | None = None,
    *,
    lock_timeout: str = "5s",
    retries: int = 3,
) -> UpgradeResult:
    """Bootstrap, then move an existing install's objects onto the keyrings.

    Safe to rerun: each pass only touches what still needs moving, and an
    object that times out on a lock is reported and left for the next run.

    Args:
        conn: Admin connection in autocommit mode, so each transfer commits
            on its own. The admin must be able to act as each old owner (a
            superuser, or the old owner itself).
        login: How the login roles authenticate.
        passwords: Passwords to set when ``login`` is ``password``.
        roles: Role names.
        old_owners: Roles whose ``public`` objects move to the owner keyring.
            Defaults to the admin.
        lock_timeout: Postgres ``lock_timeout`` for each transaction.
        retries: Extra attempts for an object that hits the lock timeout.

    Returns:
        What moved, what failed and what still needs moving.

    Raises:
        ValueError: ``conn`` is not in autocommit mode, or ``old_owners`` names
            a role that doesn't exist (checked before anything changes).
        UpgradeAbortedError: The admin or a keyring became a member of the IAM
            role; nothing after that point runs.
        psycopg.errors.LockNotAvailable: The bootstrap kept timing out on a
            lock; nothing was changed.
    """
    if not conn.autocommit:
        # Otherwise the catalog reads open an outer transaction and every
        # transfer becomes a savepoint that holds its locks until the end.
        raise ValueError("run_upgrade needs an autocommit connection")
    # Pin the parser and name resolution for the whole run, catalog reads and
    # reports included: with "$user" or a planted public operator/function on
    # a database-wide search_path, or standard_conforming_strings off, a role
    # with CREATE on the database could capture a transfer, run SQL as the
    # admin, or break the LIKE escapes in the dynamic-schema discovery.
    saved = conn.execute(
        "SELECT pg_catalog.current_setting('search_path'), "
        "pg_catalog.current_setting('standard_conforming_strings')"
    ).fetchone()
    conn.execute(
        "SELECT pg_catalog.set_config('search_path', 'pg_catalog, pg_temp', false), "
        "pg_catalog.set_config('standard_conforming_strings', 'on', false)"
    )
    try:
        return _upgrade(
            conn,
            login,
            passwords,
            roles,
            old_owners,
            lock_timeout=lock_timeout,
            retries=retries,
        )
    finally:
        if saved:
            conn.execute(
                "SELECT pg_catalog.set_config('search_path', %(p)s, false), "
                "pg_catalog.set_config('standard_conforming_strings', %(s)s, false)",
                {"p": saved[0], "s": saved[1]},
            )


def _upgrade(
    conn: psycopg.Connection,
    login: LoginMethod,
    passwords: LoginPasswords,
    roles: RoleNames,
    old_owners: Sequence[str] | None,
    *,
    lock_timeout: str,
    retries: int,
) -> UpgradeResult:
    """The body of :func:`run_upgrade`, with search_path and escapes pinned."""
    # Validate input before the bootstrap commits anything.
    if old_owners is not None:
        unknown = [
            r
            for r in old_owners
            if conn.execute(
                "SELECT pg_catalog.to_regrole(%(r)s) IS NULL", {"r": quote_ident(r)}
            ).fetchone()
            == (True,)
        ]
        if unknown:
            raise ValueError(f"--from names roles that don't exist: {unknown}")
    result = UpgradeResult(
        notes=_bootstrap_with_retries(
            conn, login, passwords, roles, lock_timeout=lock_timeout, retries=retries
        )
    )
    _assert_no_iam_lockout(conn, login, roles)
    if old_owners is None:
        row = conn.execute("SELECT session_user").fetchone()
        old_owners = [row[0]] if row else []
    old = [r for r in old_owners if r not in roles.keyrings]
    owner = quote_ident(roles.owner)
    objects = _public_objects(conn, old)
    public_owners = {o.owner for o in objects}
    # Dynamic-only owners need the dynamic keyring, not public ownership.
    keep = dict.fromkeys(_dynamic_owners(conn, roles), roles.dynamic_owner)
    keep |= dict.fromkeys(public_owners, roles.owner)
    _keep_old_owners_working(
        conn, keep, roles, result, lock_timeout=lock_timeout, retries=retries
    )
    # A view or SECURITY DEFINER routine moved ahead of something it reads
    # would lose access to it, so each stage waits until everything in the
    # earlier stages, whoever owns it, is on the owner keyring.
    for stage in (1, 2, 3):
        earlier_left = [o for o in _unmoved_public(conn, old, roles) if o.stage < stage]
        if result.failed or earlier_left:
            break
        for obj in (o for o in objects if o.stage == stage):
            _move(conn, obj, owner, login, roles, lock_timeout, retries, result)
    # A dynamic owner whose access couldn't be kept must not lose it.
    keep_failed = any(label.startswith("KEEP ACCESS ") for label, _ in result.failed)
    for schema in [] if keep_failed else _dynamic_schemas(conn, roles):
        _apply(
            conn,
            f"SCHEMA {quote_ident(schema)}",
            _dynamic_statements(conn, schema, roles),
            lock_timeout=lock_timeout,
            retries=retries,
            result=result,
        )
        _assert_no_iam_lockout(conn, login, roles)
    result.remaining_public = [
        f"{o.label} (owner {o.owner})" for o in _unmoved_public(conn, old, roles)
    ]
    if skipped := _superuser_owned_public(conn, old, roles):
        result.notes.append(
            "Not moved (owned by a superuser not named in --from; pass --from "
            f"to move them): {', '.join(skipped)}"
        )
    result.remaining_dynamic = _dynamic_schemas(conn, roles)
    if result.complete:
        result.notes.append(
            f"Retire the old login ({', '.join(old)}): once migrations use "
            f"{roles.migrator} and the app uses {roles.app}, rotate its password or "
            "drop it. It still owns the database and can act as the owner keyring."
        )
    if readers := _direct_members(conn, roles.readonly):
        result.notes.append(
            f"{roles.readonly} members (cross-tenant read; check this list): "
            f"{', '.join(readers)}"
        )
    result.kept_access = _keyring_grantees(conn, roles)
    if result.kept_access:
        revoke = "; ".join(
            f"REVOKE {quote_ident(keyring)} FROM {quote_ident(name)}"
            for keyring, name in result.kept_access
        )
        result.notes.append(f"At cutover, remove the kept access: {revoke};")
    return result


def _superuser_owned_public(
    conn: psycopg.Connection, old: Sequence[str], roles: RoleNames
) -> list[str]:
    """Count superuser-owned public objects that the completion check skips."""
    rows = conn.execute(
        SUPERUSER_OWNED_PUBLIC_SQL.encode(),
        {"old": list(old), "owner": quote_ident(roles.owner)},
    ).fetchall()
    return [f"{owner}: {count}" for owner, count in rows]


def _keep_old_owners_working(
    conn: psycopg.Connection,
    keep: dict[str, str],
    roles: RoleNames,
    result: UpgradeResult,
    *,
    lock_timeout: str,
    retries: int,
) -> None:
    """Let an app still running as an old owner keep its access until cutover.

    ``keep`` maps each owner of something about to move to the keyring that
    will own it. Topology logins never get one: app and migrator must not
    reach the owner role.
    """
    admin = conn.execute("SELECT session_user").fetchone()
    for name, keyring in sorted(keep.items()):
        # The admin's membership is the bootstrap's, by design (break-glass).
        if admin and name == admin[0]:
            continue
        if name in (*roles.keyrings, *roles.logins) or _is_superuser(conn, name):
            continue
        kept = _apply(
            conn,
            f"KEEP ACCESS {quote_ident(name)}",
            # The check runs in the grant's transaction, so a group that would
            # pass the keyring to a topology role rolls back and is reported.
            [
                _grant_membership(keyring, name),
                _do(_owner_reach_body(roles).rstrip("\n")),
            ],
            lock_timeout=lock_timeout,
            retries=retries,
            result=result,
            record=False,
        )
        if not kept:
            continue  # reported in result.failed
        result.notes.append(f"{name}: kept access through {keyring} until cutover")
        row = conn.execute(
            "SELECT rolinherit FROM pg_catalog.pg_roles WHERE rolname = %(r)s",
            {"r": name},
        ).fetchone()
        if row and not row[0]:
            result.notes.append(
                f"{name} has NOINHERIT: before PostgreSQL 16 it needs "
                f"SET ROLE {quote_ident(keyring)} (or INHERIT) to keep working"
            )


def _dynamic_owners(conn: psycopg.Connection, roles: RoleNames) -> list[str]:
    """Owners of dynamic schemas or tables that are about to move.

    Every one of them, named in ``--from`` or not: the dynamic pass moves all
    dynamic objects to the dynamic owner keyring.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT pg_catalog.pg_get_userbyid(owner) FROM (
          SELECT n.nspowner AS owner FROM pg_catalog.pg_namespace n
          WHERE n.nspname LIKE 'tables\\_%%' OR n.nspname LIKE 'custom\\_fields\\_%%'
          UNION ALL
          SELECT c.relowner FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
          WHERE (n.nspname LIKE 'tables\\_%%' OR n.nspname LIKE 'custom\\_fields\\_%%')
            AND c.relkind IN ('r', 'p')
        ) AS o
        WHERE pg_catalog.pg_get_userbyid(owner) <> %(dyn)s
        """,
        {"dyn": roles.dynamic_owner},
    ).fetchall()
    return [name for (name,) in rows]


def _keyring_grantees(
    conn: psycopg.Connection, roles: RoleNames
) -> list[tuple[str, str]]:
    """(keyring, role) for roles other than the admin and topology roles granted
    the owner or dynamic owner keyring.

    Direct grantees, so the printed REVOKE removes the access (a group role
    that holds it covers its member logins too). Read from the catalog, so
    access kept by an earlier run is reported as well.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT k.rolname, r.rolname FROM pg_catalog.pg_auth_members m
        JOIN pg_catalog.pg_roles k ON k.oid = m.roleid
        JOIN pg_catalog.pg_roles r ON r.oid = m.member
        WHERE k.rolname = ANY(%(keyrings)s)
          AND NOT r.rolsuper AND r.rolname <> session_user
          AND NOT (r.rolname = ANY(%(topology)s))
        ORDER BY 1, 2
        """,
        {
            "topology": [*roles.keyrings, *roles.logins],
            "keyrings": [roles.owner, roles.dynamic_owner],
        },
    ).fetchall()
    return [(keyring, name) for keyring, name in rows]


def _direct_members(conn: psycopg.Connection, role: str) -> list[str]:
    rows = conn.execute(
        "SELECT u.rolname FROM pg_catalog.pg_auth_members m "
        "JOIN pg_catalog.pg_roles u ON u.oid = m.member "
        "WHERE m.roleid = %(r)s::pg_catalog.regrole ORDER BY 1",
        {"r": quote_ident(role)},
    ).fetchall()
    return [name for (name,) in rows]


def _is_superuser(conn: psycopg.Connection, role: str) -> bool:
    row = conn.execute(
        "SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = %(r)s", {"r": role}
    ).fetchone()
    return bool(row and row[0])


def _move(
    conn: psycopg.Connection,
    obj: PublicObject,
    owner: str,
    login: LoginMethod,
    roles: RoleNames,
    lock_timeout: str,
    retries: int,
    result: UpgradeResult,
) -> None:
    statements = [f"ALTER {obj.kind} {obj.name} OWNER TO {owner}"]
    app = quote_ident(roles.app)
    if obj.kind == "ROUTINE":
        # Hardened installs revoke EXECUTE from PUBLIC; the old owner ran the
        # app before, so app needs it explicitly once it is the login. Never a
        # SECURITY DEFINER routine: it would now run as the owner keyring
        # (above RLS), e.g. the MCP token-revocation trigger function. Checked
        # after the ALTER in the same transaction, which then holds the
        # routine's catalog row: it can't be flipped to SECURITY DEFINER
        # between the check and the grant.
        routine = quote_literal(obj.name)
        statements.append(
            _do(
                "  IF NOT (SELECT prosecdef FROM pg_catalog.pg_proc\n"
                f"          WHERE oid = {routine}::pg_catalog.regprocedure) THEN\n"
                f"    EXECUTE pg_catalog.format('GRANT EXECUTE ON ROUTINE %s TO %I', "
                f"{routine}::pg_catalog.regprocedure, {quote_literal(roles.app)});\n"
                "  END IF;"
            )
        )
    if obj.owner == roles.app:
        # ALTER ... OWNER hands app's own ACL entries to the new owner; give
        # them back in the same transaction so a running app never loses access.
        if obj.kind == "SEQUENCE":
            statements.append(
                f"GRANT USAGE, SELECT, UPDATE ON SEQUENCE {obj.name} TO {app}"
            )
        elif obj.kind in ("TABLE", "VIEW", "MATERIALIZED VIEW", "FOREIGN TABLE"):
            statements.append(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {obj.name} TO {app}"
            )
            # Column-owned sequences moved with the table.
            statements.append(
                f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {app}"
            )

    _apply(
        conn,
        obj.label,
        statements,
        lock_timeout=lock_timeout,
        retries=retries,
        result=result,
    )
    _assert_no_iam_lockout(conn, login, roles)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m tracecat.db.roles``."""
    try:
        return _main(argv)
    except (
        psycopg.errors.RaiseException,
        psycopg.errors.LockNotAvailable,
        UpgradeAbortedError,
        ValueError,
    ) as exc:
        # A safety check refused; its message says why and what to do.
        diag = exc.diag if isinstance(exc, psycopg.Error) else None
        message = diag.message_primary if diag and diag.message_primary else str(exc)
        print(f"error: {_printable(message)}", file=sys.stderr)
        if diag and diag.message_hint:
            print(f"hint: {_printable(diag.message_hint)}", file=sys.stderr)
        return 1


def _main(argv: Sequence[str] | None) -> int:
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
    upgrade = commands.add_parser(
        "upgrade",
        help="Bootstrap, then move an existing install's objects onto the keyrings.",
    )
    upgrade.add_argument(
        "--login",
        type=LoginMethod,
        choices=list(LoginMethod),
        default=LoginMethod.PASSWORD,
        help="How the login roles authenticate (default: password).",
    )
    upgrade.add_argument(
        "--from",
        dest="old_owners",
        action="append",
        metavar="ROLE",
        help="Role whose public objects move to tracecat_owner (repeatable; "
        "default: the admin).",
    )
    args = parser.parse_args(argv)

    login: LoginMethod = args.login
    if args.command == "upgrade":
        with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
            result = run_upgrade(
                conn, login, LoginPasswords.from_config(), old_owners=args.old_owners
            )
        for note in result.notes:
            print(_printable(note), file=sys.stderr)
        print(f"moved: {len(result.moved)}", file=sys.stderr)
        for label, reason in result.failed:
            print(f"failed: {_printable(label)}: {_printable(reason)}", file=sys.stderr)
        for label in (*result.remaining_public, *result.remaining_dynamic):
            print(f"still to move: {_printable(label)}", file=sys.stderr)
        print(f"{DYNAMIC_OWNER_ENV_VAR}={DEFAULT_ROLES.dynamic_owner}")
        return 0 if result.complete else 1
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
           ) AS owns_public_table,
           -- The database owner owns public (PG 15+) and can drop it.
           EXISTS (
             SELECT FROM pg_catalog.pg_database d
             WHERE d.datname = pg_catalog.current_database() AND d.datdba = r.oid
           ) OR EXISTS (
             SELECT FROM pg_catalog.pg_namespace n
             WHERE n.nspname = 'public' AND n.nspowner = r.oid
           ) AS owns_database
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
    for name, is_super, bypass, createrole, replication, owns, owns_db in rows:
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
        if owns_db:
            problems.append(f"{name} owns the database or the public schema")
    if problems:
        raise UnsafeDatabaseLoginError(
            "TRACECAT__RLS_MODE=enforce needs a login without SUPERUSER, BYPASSRLS, "
            "CREATEROLE or REPLICATION that owns no public table: "
            + "; ".join(problems)
            + ". Log in as the 'app' role (python -m tracecat.db.roles bootstrap)."
        )


if __name__ == "__main__":
    raise SystemExit(main())
