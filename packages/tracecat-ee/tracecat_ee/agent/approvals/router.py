"""EE Approvals API router for submitting approval decisions."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.types import ToolApproved, ToolDenied
from tracecat.auth.dependencies import WorkspaceUserRouteRole as WorkspaceUserRole
from tracecat.chat.schemas import ApprovalDecision, ContinueRunRequest
from tracecat.db.engine import get_async_session
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger
from tracecat_ee.agent.approvals.service import ApprovalMap

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalSubmission(BaseModel):
    """Request model for submitting approval decisions."""

    approvals: ApprovalMap


def _to_approval_decisions(approvals: ApprovalMap) -> list[ApprovalDecision]:
    decisions: list[ApprovalDecision] = []
    for tool_call_id, value in approvals.items():
        if isinstance(value, bool):
            decisions.append(
                ApprovalDecision(
                    tool_call_id=tool_call_id,
                    action="approve" if value else "deny",
                    reason=None if value else "Tool denied by user",
                )
            )
            continue
        if isinstance(value, ToolApproved):
            if value.override_args:
                decisions.append(
                    ApprovalDecision(
                        tool_call_id=tool_call_id,
                        action="override",
                        override_args=value.override_args,
                    )
                )
            else:
                decisions.append(
                    ApprovalDecision(
                        tool_call_id=tool_call_id,
                        action="approve",
                    )
                )
            continue
        if isinstance(value, ToolDenied):
            decisions.append(
                ApprovalDecision(
                    tool_call_id=tool_call_id,
                    action="deny",
                    reason=value.message or "Tool denied by user",
                )
            )
            continue
        raise ValueError(
            "Invalid approval payload for tool call "
            f"'{tool_call_id}': expected bool, ToolApproved, or ToolDenied."
        )
    return decisions


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

    try:
        decisions = _to_approval_decisions(payload.approvals)
        continuation = ContinueRunRequest(
            decisions=decisions,
            source="inbox",
        )
        await session_service.run_turn(session_id, continuation)
    except TracecatNotFoundError as exc:
        logger.warning(
            "Agent session not found while submitting approvals",
            session_id=session_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        logger.warning(
            "Failed to submit approvals",
            session_id=session_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception(
            "Unexpected error while submitting approvals",
            session_id=session_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit approvals",
        ) from exc
