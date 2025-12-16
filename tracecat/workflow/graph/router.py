"""Graph API router for workflow graph operations.

This module provides the canonical graph API endpoints:
- GET /workflows/{id}/graph - Read the graph projection from Actions
- PATCH /workflows/{id}/graph - Apply graph operations with optimistic concurrency
"""

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers.workflow import AnyWorkflowIDPath
from tracecat.workflow.graph.service import WorkflowGraphService
from tracecat.workflow.management.schemas import (
    GraphOperationsRequest,
    GraphResponse,
)

router = APIRouter(
    prefix="/workflows/{workflow_id}/graph",
    tags=["graph"],
)


@router.get("")
async def get_graph(
    role: WorkspaceUserRole,
    workflow_id: AnyWorkflowIDPath,
    session: AsyncDBSession,
) -> GraphResponse:
    """Get the canonical graph projection for a workflow.

    Returns the graph built from Actions (single source of truth),
    not from Workflow.object.
    """
    svc = WorkflowGraphService(session, role=role)
    graph = await svc.get_graph(workflow_id)
    if graph is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    return graph


@router.patch("")
async def apply_graph_operations(
    role: WorkspaceUserRole,
    workflow_id: AnyWorkflowIDPath,
    request: GraphOperationsRequest,
    session: AsyncDBSession,
) -> GraphResponse:
    """Apply graph operations with optimistic concurrency.

    Validates base_version matches current graph_version.
    Returns 409 Conflict with latest graph if versions mismatch.
    """
    svc = WorkflowGraphService(session, role=role)
    try:
        graph = await svc.apply_operations(
            workflow_id=workflow_id,
            base_version=request.base_version,
            operations=request.operations,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    if graph is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    return graph
