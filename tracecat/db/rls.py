"""PostgreSQL Row-Level Security (RLS) context management.

This module provides functions to set and clear RLS context variables in PostgreSQL
sessions, enabling database-level multi-tenancy isolation.

RLS works by setting PostgreSQL session variables that are checked by row security
policies attached to tables. When a query is executed, PostgreSQL automatically
filters rows based on whether the current session's context matches the row's
organization_id or workspace_id.

Key features:
- Uses SET LOCAL to scope variables to the current transaction (connection pool safe)
- Supports bypassing RLS for system operations via special "bypass" UUID
- Integrates with ctx_role context variable for automatic context propagation
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select, text

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

# Special UUID value that bypasses RLS checks when set as the context
# Policies check: IF context == bypass_value THEN allow_all
RLS_BYPASS_VALUE = "00000000-0000-0000-0000-000000000000"


def is_rls_enabled() -> bool:
    """Check if RLS feature flag is enabled."""
    return FeatureFlag.RLS_ENABLED in config.TRACECAT__FEATURE_FLAGS


async def set_rls_context(
    session: AsyncSession,
    org_id: uuid.UUID | str | None,
    workspace_id: uuid.UUID | str | None,
    user_id: uuid.UUID | str | None = None,
) -> None:
    """Set RLS context variables in the PostgreSQL session.

    Uses SET LOCAL to scope variables to the current transaction, making it safe
    for use with connection pooling - the variables are automatically cleared
    when the transaction ends.

    Args:
        session: The SQLAlchemy async session
        org_id: Organization ID to set, or None to use bypass value
        workspace_id: Workspace ID to set, or None to use bypass value
        user_id: Optional user ID for audit purposes
    """
    if not is_rls_enabled():
        return

    # Convert to strings, using bypass value for None
    org_id_str = str(org_id) if org_id else RLS_BYPASS_VALUE
    workspace_id_str = str(workspace_id) if workspace_id else RLS_BYPASS_VALUE
    user_id_str = str(user_id) if user_id else RLS_BYPASS_VALUE

    logger.trace(
        "Setting RLS context",
        org_id=org_id_str,
        workspace_id=workspace_id_str,
        user_id=user_id_str,
    )

    # Use SET LOCAL to scope to current transaction
    # This is safe with connection pooling since the settings are cleared on transaction end
    await session.execute(
        text(f"SET LOCAL {RLS_VAR_ORG_ID} = :org_id"),
        {"org_id": org_id_str},
    )
    await session.execute(
        text(f"SET LOCAL {RLS_VAR_WORKSPACE_ID} = :workspace_id"),
        {"workspace_id": workspace_id_str},
    )
    await session.execute(
        text(f"SET LOCAL {RLS_VAR_USER_ID} = :user_id"),
        {"user_id": user_id_str},
    )


async def set_rls_context_from_role(
    session: AsyncSession,
    role: Role | None = None,
) -> None:
    """Set RLS context from a Role object or the current ctx_role.

    If role is None, reads from ctx_role context variable. If ctx_role is also None,
    sets bypass context (allowing full access - for system operations).

    Args:
        session: The SQLAlchemy async session
        role: Optional Role object. If None, reads from ctx_role.
    """
    if not is_rls_enabled():
        return

    # Try to get role from argument or context
    effective_role = role or ctx_role.get()

    if effective_role is None:
        # No role context - set bypass for system operations
        logger.trace("No role context, setting RLS bypass")
        await set_rls_context(
            session,
            org_id=None,
            workspace_id=None,
            user_id=None,
        )
        return

    await set_rls_context(
        session,
        org_id=effective_role.organization_id,
        workspace_id=effective_role.workspace_id,
        user_id=effective_role.user_id,
    )


async def clear_rls_context(session: AsyncSession) -> None:
    """Clear RLS context variables by setting them to bypass values.

    This effectively disables RLS filtering for subsequent queries in the session.
    Typically called when switching to a system context or cleaning up.

    Args:
        session: The SQLAlchemy async session
    """
    if not is_rls_enabled():
        return

    logger.trace("Clearing RLS context")
    await set_rls_context(
        session,
        org_id=None,
        workspace_id=None,
        user_id=None,
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
