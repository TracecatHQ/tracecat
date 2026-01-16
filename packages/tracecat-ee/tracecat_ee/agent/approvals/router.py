"""EE Approvals API router for submitting approval decisions."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError

from tracecat.agent.session.service import AgentSessionService
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.engine import get_async_session
from tracecat.dsl.client import get_temporal_client
from tracecat.logger import logger
from tracecat_ee.agent.approvals.service import ApprovalMap
from tracecat_ee.agent.types import AgentWorkflowID
from tracecat_ee.agent.workflows.durable import (
    DurableAgentWorkflow,
    WorkflowApprovalSubmission,
)

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalSubmission(BaseModel):
    """Request model for submitting approval decisions."""

    approvals: ApprovalMap


@router.post("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def submit_approvals(
    *,
    role: WorkspaceUserRole,
    session_id: uuid.UUID,
    payload: ApprovalSubmission,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Submit approval decisions to a running agent workflow.

    This endpoint sends approval decisions back to an agent workflow
    that is waiting for human-in-the-loop approval on tool calls.

    Args:
        role: The authenticated user role.
        session_id: The agent session ID (used to lookup the workflow).
        payload: The approval decisions mapping tool_call_id to decision.
        session: Database session for workspace-scoped lookups.

    Raises:
        HTTPException 400: If the approval submission fails validation.
        HTTPException 404: If the agent session/workflow is not found.
        HTTPException 502: If communication with Temporal fails.
        HTTPException 500: For unexpected errors.
    """
    workspace_id = role.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace access required",
        )

    # Verify the session belongs to the caller's workspace
    # This prevents cross-workspace access if an attacker knows another workspace's session_id
    session_service = AgentSessionService(session, role)
    agent_session = await session_service.get_session(session_id)
    if agent_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent session not found",
        )

    # Use the session's current run_id to target the correct workflow
    if agent_session.curr_run_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active workflow run for this session",
        )

    workflow_id = AgentWorkflowID(agent_session.curr_run_id)
    client = await get_temporal_client()
    handle = client.get_workflow_handle_for(DurableAgentWorkflow.run, workflow_id)

    try:
        submission = WorkflowApprovalSubmission(
            approvals=payload.approvals,
            approved_by=role.user_id,
        )
        await handle.execute_update(DurableAgentWorkflow.set_approvals, submission)
    except ApplicationError as exc:
        logger.warning(
            "Failed to submit approvals",
            session_id=session_id,
            workflow_id=workflow_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RPCError as exc:
        logger.error(
            "Temporal RPC error while submitting approvals",
            session_id=session_id,
            workflow_id=workflow_id,
            error=str(exc),
        )
        if "workflow not found" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent session not found",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to reach workflow service",
        ) from exc
    except Exception as exc:
        logger.exception(
            "Unexpected error while submitting approvals",
            session_id=session_id,
            workflow_id=workflow_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit approvals",
        ) from exc
