from __future__ import annotations

from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Workspace
from tracecat.identifiers import OrganizationID, WorkspaceID


@activity.defn
async def get_workspace_organization_id_activity(
    workspace_id: WorkspaceID,
) -> OrganizationID:
    """Resolve organization_id for a workspace."""
    async with get_async_session_context_manager() as session:
        stmt = select(Workspace.organization_id).where(Workspace.id == workspace_id)
        result = await session.execute(stmt)
        org_id = result.scalar_one_or_none()
    if org_id is None:
        raise ApplicationError(
            f"Workspace {workspace_id} not found or has no organization",
            non_retryable=True,
        )
    return org_id
