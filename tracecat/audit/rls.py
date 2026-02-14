"""RLS-specific audit logging functions.

This module provides functions for auditing RLS-related events:
- Context establishment (when RLS variables are set)
- Access violations (when RLS blocks a query)

These events are important for security monitoring and compliance.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.auth.types import Role


def audit_rls_context_set(
    session_id: uuid.UUID | str | None,
    role: Role | None,
    org_id: uuid.UUID | str | None,
    workspace_id: uuid.UUID | str | None,
) -> None:
    """Log when RLS context is established for a database session.

    This is called whenever RLS context variables are set in PostgreSQL,
    providing an audit trail of who accessed which tenant.

    Args:
        session_id: Unique identifier for the database session (if available)
        role: The Role object used to set context (if available)
        org_id: Organization ID that was set
        workspace_id: Workspace ID that was set
    """
    logger.info(
        "rls.context_set",
        session_id=str(session_id) if session_id else None,
        role_type=role.type if role else None,
        role_user_id=str(role.user_id) if role and role.user_id else None,
        role_service_id=role.service_id if role else None,
        org_id=str(org_id) if org_id else None,
        workspace_id=str(workspace_id) if workspace_id else None,
    )


def audit_rls_violation(
    table: str,
    operation: str,
    record_id: uuid.UUID | str | None,
    role: Role | None,
) -> None:
    """Log when RLS blocks an access attempt.

    This is called when a query returns no results due to RLS filtering,
    indicating a potential unauthorized access attempt or misconfigured context.

    Args:
        table: Name of the table where access was blocked
        operation: The operation that was attempted (e.g., "select", "update")
        record_id: ID of the record that was attempted to be accessed (if known)
        role: The Role object that was active when the violation occurred
    """
    logger.warning(
        "rls.violation",
        table=table,
        operation=operation,
        record_id=str(record_id) if record_id else None,
        role_type=role.type if role else None,
        role_user_id=str(role.user_id) if role and role.user_id else None,
        role_org_id=str(role.organization_id) if role else None,
        role_workspace_id=str(role.workspace_id)
        if role and role.workspace_id
        else None,
    )


def audit_rls_bypass(
    reason: str,
    role: Role | None = None,
) -> None:
    """Log when RLS is intentionally bypassed.

    This is called when a system operation runs without RLS context,
    providing an audit trail for elevated-privilege operations.

    Args:
        reason: Description of why RLS is being bypassed
        role: The Role object if available (may be None for system operations)
    """
    logger.info(
        "rls.bypass",
        reason=reason,
        role_type=role.type if role else None,
        role_user_id=str(role.user_id) if role and role.user_id else None,
        role_service_id=role.service_id if role else None,
    )
