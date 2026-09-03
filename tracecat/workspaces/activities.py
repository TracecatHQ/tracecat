from __future__ import annotations

from sqlalchemy import select
from temporalio import activity

from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Workspace
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
)
from tracecat.temporal.errors import (
    activity_error_boundary,
    raise_application_error_from_classification,
)


def _workspace_resolution_unavailable(
    error: Exception,
) -> RuntimeErrorClassification:
    return RuntimeErrorClassification.platform(
        kind=RuntimeErrorKind.WORKFLOW_BOOTSTRAP_UNAVAILABLE,
        message="Tracecat could not resolve the workflow workspace",
        retry_disposition=RetryDisposition.RETRYABLE,
        cause=error,
    )


@activity.defn
async def get_workspace_organization_id_activity(
    workspace_id: WorkspaceID,
) -> OrganizationID:
    """Resolve organization_id for a workspace."""
    with activity_error_boundary(_workspace_resolution_unavailable):
        async with get_async_session_bypass_rls_context_manager() as session:
            stmt = select(Workspace.organization_id).where(Workspace.id == workspace_id)
            result = await session.execute(stmt)
            org_id = result.scalar_one_or_none()
    if org_id is None:
        classification = RuntimeErrorClassification.platform(
            kind=RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA,
            message="Tracecat could not resolve the workflow workspace",
            retry_disposition=RetryDisposition.NON_RETRYABLE,
        )
        raise_application_error_from_classification(classification)
    return org_id
