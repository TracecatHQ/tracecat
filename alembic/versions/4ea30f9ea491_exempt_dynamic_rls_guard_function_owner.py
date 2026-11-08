"""exempt the dynamic rls guard's function owner

Revision ID: 4ea30f9ea491
Revises: b4074a0896b5
Create Date: 2026-11-07 12:00:00.000000

b4074a0896b5's guard lets a session through when its login role
(`session_user`) is a member of the guard's event trigger owner. On RDS the
owner of an event trigger that an `rds_superuser` member creates is
`rdsadmin`, and the migration role isn't a member of `rdsadmin`. So on RDS
the guard also binds migrations: b4074a0896b5's claim that migrations keep
full DDL rights on dynamic tables doesn't hold there.

This revision exempts members of the guard function's owner instead. That
is the role that installed the guard: the migration role that ran
b4074a0896b5, or the admin who ran its `MANUAL_INSTALL_SQL`. Elsewhere the
function's owner and the triggers' owner are the same role, so nothing
changes. Members of the function's owner could already rewrite the guard, so
exempting them grants nothing new. The check is still on `session_user`, so
`SET ROLE` still doesn't lift the guard, and superusers stay exempt.

The upgrade only replaces the function's body (`CREATE OR REPLACE
FUNCTION`). That needs the function's owner, not a superuser, so it works on
RDS, and it keeps the triggers. A guard that already has this body is left
alone. The upgrade changes nothing and logs a warning when:
- the guard isn't installed or its triggers don't fire, for example because
  b4074a0896b5 skipped it: install it with this revision's
  `MANUAL_INSTALL_SQL` as a superuser;
- the migration role doesn't own the function (an admin installed it): run
  this revision's `MANUAL_INSTALL_SQL` as a superuser, or this upgrade as the
  function's owner.

Downgrade puts b4074a0896b5's body back the same way.

Uninstalling on RDS: the triggers belong to `rdsadmin`, and Postgres lets
only an event trigger's owner (or a superuser) drop it. RDS documents that
its master user can create, modify, rename and delete event triggers, so run
`UNINSTALL_STATEMENTS` as the master user. Other `rds_superuser` members may
be refused.
"""

import importlib.util
import logging
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import psycopg.errors
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4ea30f9ea491"
down_revision: str | None = "b4074a0896b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def _load_previous() -> ModuleType:
    """Load b4074a0896b5, whose guard this revision amends."""
    path = Path(__file__).with_name(
        "b4074a0896b5_guard_rls_on_dynamic_workspace_tables.py"
    )
    spec = importlib.util.spec_from_file_location("_dynamic_rls_guard_b4074", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_previous = _load_previous()

GUARD_FUNCTION: str = _previous.GUARD_FUNCTION

_OLD_EXEMPTION = """\
    SELECT evtowner INTO guard_owner
    FROM pg_event_trigger
    WHERE evtfoid = 'public.tracecat_guard_dynamic_rls()'::regprocedure
      AND evtevent = TG_EVENT
    LIMIT 1;
"""
_NEW_EXEMPTION = """\
    -- The function's owner, not the triggers': on RDS, rdsadmin owns the
    -- event triggers an rds_superuser member creates.
    SELECT proowner INTO guard_owner
    FROM pg_proc
    WHERE oid = 'public.tracecat_guard_dynamic_rls()'::regprocedure;
"""
_OLD_HINT = """\
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'Only the role that owns this event trigger, or a '
                         'member of it, can weaken RLS on workspace tables.';
"""
_NEW_HINT = """\
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'Only the owner of '
                         'public.tracecat_guard_dynamic_rls(), or a member '
                         'of it, can weaken RLS on workspace tables.';
"""


def _replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError("b4074a0896b5's guard body changed; update this revision")
    return source.replace(old, new)


_GUARD_FUNCTION_SQL = _replace_once(
    _replace_once(_previous._GUARD_FUNCTION_SQL, _OLD_EXEMPTION, _NEW_EXEMPTION),
    _OLD_HINT,
    _NEW_HINT,
)

GUARD_STATEMENTS: tuple[str, ...] = (
    _GUARD_FUNCTION_SQL,
    *_previous.GUARD_STATEMENTS[1:],
)
"""The guard function and its two event triggers, in install order."""

UNINSTALL_STATEMENTS: tuple[str, ...] = _previous.UNINSTALL_STATEMENTS

MANUAL_INSTALL_SQL = ";\n".join((*UNINSTALL_STATEMENTS, *GUARD_STATEMENTS)) + ";\n"
"""Drop and reinstall the guard, for an admin to run by hand as a superuser.

The installing role owns the function and is exempt from the guard.
"""


def _body(function_sql: str) -> str:
    return function_sql.split("$guard$")[1]


def _guard_function_state() -> tuple[str, str] | None:
    """Return the guard function's (body, owner), or None if it doesn't exist."""
    row = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT prosrc, pg_get_userbyid(proowner) FROM pg_proc
                WHERE oid = to_regprocedure(:function)
                """
            ),
            {"function": GUARD_FUNCTION},
        )
        .one_or_none()
    )
    return None if row is None else (row[0], row[1])


def _set_guard_body(function_sql: str, manual_revision: str) -> None:
    """Replace the guard function's body in place, keeping its triggers."""
    state = _guard_function_state()
    if state is None:
        return
    body, owner = state
    if body == _body(function_sql):
        logger.info("Dynamic-table RLS guard already has this body; keeping it.")
        return
    bind = op.get_bind()
    try:
        with bind.begin_nested():
            bind.execute(sa.text(function_sql))
    except DBAPIError as exc:
        if not isinstance(exc.orig, psycopg.errors.InsufficientPrivilege):
            raise
        role = bind.execute(sa.text("SELECT session_user")).scalar_one()
        logger.warning(
            "Kept the dynamic-table RLS guard's body: role %r can't replace "
            "%s, which %r owns. Run MANUAL_INSTALL_SQL from revision %s as a "
            "superuser, or run this migration as %r. Error: %s",
            role,
            GUARD_FUNCTION,
            owner,
            manual_revision,
            owner,
            exc.orig,
        )


def upgrade() -> None:
    if not _previous._guard_firing():
        logger.warning(
            "Dynamic-table RLS guard isn't installed or doesn't fire; left it "
            "unchanged. Until an admin installs it, the app role can switch "
            "RLS off on tables_* and custom_fields_* tables. Install it before "
            "setting TRACECAT__RLS_MODE=enforce: run MANUAL_INSTALL_SQL from "
            "revision %s as a superuser.",
            revision,
        )
        return
    _set_guard_body(_GUARD_FUNCTION_SQL, revision)


def downgrade() -> None:
    _set_guard_body(_previous._GUARD_FUNCTION_SQL, _previous.revision)
