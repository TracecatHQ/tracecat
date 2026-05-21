from __future__ import annotations

from sqlalchemy import select
from temporalio import activity

from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Workspace
from tracecat.dsl import activity_errors as dsl_errors
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.runtime.errors import RuntimeErrorPhase


@activity.defn
async def get_workspace_organization_id_activity(
    workspace_id: WorkspaceID,
) -> OrganizationID:
    """Resolve organization_id for a workspace."""
    with dsl_errors.platform_or_infra_boundary(
        code="workspace.organization.resolve_failed",
        message="Failed to resolve workspace organization",
        phase=RuntimeErrorPhase.PREPARE,
        ref=str(workspace_id),
    ):
        async with get_async_session_bypass_rls_context_manager() as session:
            stmt = select(Workspace.organization_id).where(Workspace.id == workspace_id)
            result = await session.execute(stmt)
            org_id = result.scalar_one_or_none()
    if org_id is None:
        raise dsl_errors.user(
            code="workspace.organization.not_found",
            message=f"Workspace {workspace_id} not found or has no organization",
            phase=RuntimeErrorPhase.PREPARE,
            error_type="WorkspaceOrganizationNotFound",
            ref=str(workspace_id),
        )
    return org_id
