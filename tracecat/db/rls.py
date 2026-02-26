"""PostgreSQL Row-Level Security (RLS) context management.

This module provides functions to set and clear RLS context variables in PostgreSQL
sessions, enabling database-level multi-tenancy isolation.

RLS works by setting PostgreSQL session variables that are checked by row security
policies attached to tables. When a query is executed, PostgreSQL automatically
filters rows based on whether the current session's context matches the row's
organization_id or workspace_id.

Key features:
- Uses transaction-local `set_config(..., true)` settings (connection pool safe)
- Supports explicit bypassing via app.rls_bypass for privileged operations
- Integrates with ctx_role context variable for automatic context propagation
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlalchemy.orm
from sqlalchemy import event, select, text
from sqlalchemy.engine import Connection

from tracecat import config
from tracecat.audit.rls import audit_rls_violation
from tracecat.contexts import ctx_role
from tracecat.exceptions import TracecatRLSViolationError
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.logger import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.auth.types import Role

# PostgreSQL session variable names for RLS context
RLS_VAR_ORG_ID = "app.current_org_id"
RLS_VAR_WORKSPACE_ID = "app.current_workspace_id"
RLS_VAR_USER_ID = "app.current_user_id"
RLS_VAR_BYPASS = "app.rls_bypass"

RLS_BYPASS_ON = "on"
RLS_BYPASS_OFF = "off"
RLS_UNSET_VALUE = ""

_RLS_CONTEXT_INFO_KEY = "tracecat_rls_context"


@dataclass(frozen=True, slots=True)
class _RLSContext:
    """Session-level RLS context cached for transaction re-application."""

    org_id: str | None
    workspace_id: str | None
    user_id: str | None
    bypass: bool


def is_rls_enabled() -> bool:
    """Check if RLS checks should be treated as enabled in application logic.

    Primary source of truth is TRACECAT__RLS_MODE. The legacy rls-enabled
    feature flag remains supported for backwards compatibility.
    """
    return (
        config.TRACECAT__RLS_MODE != config.RLSMode.OFF
        or FeatureFlag.RLS_ENABLED in config.TRACECAT__FEATURE_FLAGS
    )


def is_rls_mode_off() -> bool:
    """Check whether runtime RLS mode is OFF."""
    return config.TRACECAT__RLS_MODE == config.RLSMode.OFF


def is_rls_mode_shadow() -> bool:
    """Check whether runtime RLS mode is SHADOW."""
    return config.TRACECAT__RLS_MODE == config.RLSMode.SHADOW


def is_rls_mode_enforce() -> bool:
    """Check whether runtime RLS mode is ENFORCE."""
    return config.TRACECAT__RLS_MODE == config.RLSMode.ENFORCE


def _normalize_rls_context(
    *,
    org_id: uuid.UUID | str | None,
    workspace_id: uuid.UUID | str | None,
    user_id: uuid.UUID | str | None,
    bypass: bool,
) -> _RLSContext:
    return _RLSContext(
        org_id=str(org_id) if org_id else None,
        workspace_id=str(workspace_id) if workspace_id else None,
        user_id=str(user_id) if user_id else None,
        bypass=bypass,
    )


def _cache_rls_context(session: AsyncSession, context: _RLSContext) -> None:
    """Store RLS context on the sync session so event hooks can reapply it."""
    session.sync_session.info[_RLS_CONTEXT_INFO_KEY] = context


_RLS_CONTEXT_SQL = text(
    f"""
    SELECT
        set_config('{RLS_VAR_BYPASS}', :bypass, true),
        set_config('{RLS_VAR_ORG_ID}', :org_id, true),
        set_config('{RLS_VAR_WORKSPACE_ID}', :workspace_id, true),
        set_config('{RLS_VAR_USER_ID}', :user_id, true)
    """
)


def _build_rls_context_params(context: _RLSContext) -> dict[str, str]:
    return {
        "bypass": RLS_BYPASS_ON if context.bypass else RLS_BYPASS_OFF,
        "org_id": context.org_id if context.org_id is not None else RLS_UNSET_VALUE,
        "workspace_id": context.workspace_id
        if context.workspace_id is not None
        else RLS_UNSET_VALUE,
        "user_id": context.user_id if context.user_id is not None else RLS_UNSET_VALUE,
    }


def _apply_rls_context_sync(connection: Connection, context: _RLSContext) -> None:
    """Apply context for the current transaction on a sync SQLAlchemy connection."""
    connection.execute(_RLS_CONTEXT_SQL, _build_rls_context_params(context))


async def _apply_rls_context_async(
    session: AsyncSession,
    context: _RLSContext,
) -> None:
    """Apply context for the current transaction on an async SQLAlchemy session."""
    await session.execute(_RLS_CONTEXT_SQL, _build_rls_context_params(context))


@event.listens_for(sqlalchemy.orm.Session, "after_begin")
def _reapply_rls_context_after_begin(  # pyright: ignore[reportUnusedFunction] - SQLAlchemy event listener
    session: sqlalchemy.orm.Session,
    transaction: sqlalchemy.orm.SessionTransaction,  # noqa: ARG001
    connection: Connection,
) -> None:
    """Reapply cached RLS context whenever a new transaction begins."""
    context = session.info.get(_RLS_CONTEXT_INFO_KEY)
    if not isinstance(context, _RLSContext):
        return
    _apply_rls_context_sync(connection, context)


async def set_rls_context(
    session: AsyncSession,
    org_id: uuid.UUID | str | None,
    workspace_id: uuid.UUID | str | None,
    user_id: uuid.UUID | str | None = None,
    *,
    bypass: bool = False,
) -> None:
    """Set RLS context variables in the PostgreSQL session.

    Uses transaction-local settings (`set_config(..., true)`) to scope values to
    the current transaction, making it safe for use with connection pooling.

    Args:
        session: The SQLAlchemy async session
        org_id: Organization ID to set. None removes tenant org scope.
        workspace_id: Workspace ID to set. None removes tenant workspace scope.
        user_id: Optional user ID for audit purposes
        bypass: Whether to bypass tenant filtering in RLS policies.
    """
    context = _normalize_rls_context(
        org_id=org_id,
        workspace_id=workspace_id,
        user_id=user_id,
        bypass=bypass,
    )

    logger.trace(
        "Setting RLS context",
        org_id=context.org_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        bypass=context.bypass,
    )

    _cache_rls_context(session, context)
    await _apply_rls_context_async(session, context)


async def set_rls_context_from_role(
    session: AsyncSession,
    role: Role | None = None,
) -> None:
    """Set RLS context from a Role object or the current ctx_role.

    If role is None, reads from ctx_role context variable. If neither is available,
    sets deny-by-default context (`app.rls_bypass=off` and no tenant IDs).

    Args:
        session: The SQLAlchemy async session
        role: Optional Role object. If None, reads from ctx_role.
    """
    # Try to get role from argument or context
    effective_role = role or ctx_role.get()

    if effective_role is None:
        logger.trace("No role context, setting RLS deny-default context")
        await set_rls_context(
            session,
            org_id=None,
            workspace_id=None,
            user_id=None,
            bypass=False,
        )
        return

    if effective_role.is_platform_superuser:
        logger.trace(
            "Platform superuser context, enabling RLS bypass",
            user_id=str(effective_role.user_id) if effective_role.user_id else None,
        )
        await set_rls_context(
            session,
            org_id=None,
            workspace_id=None,
            user_id=effective_role.user_id,
            bypass=True,
        )
        return

    await set_rls_context(
        session,
        org_id=effective_role.organization_id,
        workspace_id=effective_role.workspace_id,
        user_id=effective_role.user_id,
        bypass=False,
    )


async def clear_rls_context(session: AsyncSession) -> None:
    """Clear RLS context variables and enforce deny-by-default mode.

    Args:
        session: The SQLAlchemy async session
    """
    logger.trace("Clearing RLS context")
    await set_rls_context(
        session,
        org_id=None,
        workspace_id=None,
        user_id=None,
        bypass=False,
    )


async def verify_rls_access(
    session: AsyncSession,
    table_class: type,
    record_id: uuid.UUID,
) -> bool:
    """Check if a record is accessible under the current RLS context.

    PostgreSQL RLS returns empty results (not errors) when access is denied.
    This function explicitly checks if a record can be accessed.

    Args:
        session: The SQLAlchemy async session
        table_class: The SQLAlchemy model class for the table
        record_id: The primary key (id) of the record to check

    Returns:
        True if the record is accessible, False otherwise
    """
    # Try to fetch the record under current RLS context
    # If RLS blocks access, the query returns None
    result = await session.execute(
        select(table_class).where(table_class.id == record_id)
    )
    return result.scalar_one_or_none() is not None


async def require_rls_access(
    session: AsyncSession,
    table_class: type,
    record_id: uuid.UUID,
    operation: str = "access",
) -> None:
    """Require that a record is accessible under the current RLS context.

    Raises TracecatRLSViolationError if the record cannot be accessed.
    Use this for explicit access checks on sensitive operations.

    Args:
        session: The SQLAlchemy async session
        table_class: The SQLAlchemy model class for the table
        record_id: The primary key (id) of the record to check
        operation: Description of the operation being performed (for error messages)

    Raises:
        TracecatRLSViolationError: If RLS blocks access to the record
    """
    if not is_rls_enabled():
        return

    if not await verify_rls_access(session, table_class, record_id):
        role = ctx_role.get()
        # Log the violation for audit purposes
        audit_rls_violation(
            table=table_class.__tablename__,
            operation=operation,
            record_id=record_id,
            role=role,
        )
        raise TracecatRLSViolationError(
            f"RLS blocked {operation} on {table_class.__tablename__}",
            table=table_class.__tablename__,
            operation=operation,
            org_id=str(role.organization_id) if role else None,
            workspace_id=str(role.workspace_id) if role and role.workspace_id else None,
        )
