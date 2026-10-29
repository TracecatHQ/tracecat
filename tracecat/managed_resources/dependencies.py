"""FastAPI dependencies that apply the managed write guard to whole routers."""

from __future__ import annotations

from fastapi import Depends, Request

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers.workflow import AnyWorkflowIDPath, WorkflowUUID
from tracecat.managed_resources.guard import ensure_unmanaged
from tracecat.workspace_sync.enums import SyncResourceType

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def refuse_managed_workflow_writes(
    request: Request,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> None:
    """Refuse every non-read request that targets a managed workflow.

    Used on routers whose every write edits the workflow's definition: its
    actions, graph and tags. ``workflow_id`` comes from the path or the query,
    whichever the route declares.

    Raises:
        ManagedResourceLockedError: The workflow is managed.
    """
    if request.method in _READ_METHODS:
        return
    await ensure_unmanaged(
        session,
        workspace_id=role.workspace_id,
        resource_type=SyncResourceType.WORKFLOW,
        local_id=WorkflowUUID.new(workflow_id),
    )


ManagedWorkflowWriteGuard = Depends(refuse_managed_workflow_writes)
"""Router or route dependency that applies :func:`refuse_managed_workflow_writes`."""
