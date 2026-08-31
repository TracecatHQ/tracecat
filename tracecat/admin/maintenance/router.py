"""Superuser-only platform maintenance endpoints."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.service import RPCError, RPCStatusCode

from tracecat import config
from tracecat.admin.maintenance.schemas import (
    CaseAgentSessionBackfillStatus,
    CaseAgentSessionInteractionBackfillResponse,
    CaseAgentSessionInteractionBackfillStartResponse,
    CaseAgentSessionInteractionBackfillStatusResponse,
)
from tracecat.auth.credentials import SuperuserRole
from tracecat.cases.agent_sessions.workflow import (
    CaseAgentSessionBackfillWorkflow,
)
from tracecat.dsl.client import get_temporal_client

router = APIRouter(prefix="/maintenance", tags=["admin:maintenance"])
_BACKFILL_WORKFLOW_ID = "case-agent-session-interactions-backfill"


@router.post(
    "/case-agent-session-interactions/backfill",
    response_model=CaseAgentSessionInteractionBackfillStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_case_agent_session_interaction_backfill(
    role: SuperuserRole,
) -> CaseAgentSessionInteractionBackfillStartResponse:
    """Start or join the durable historical case-mutation backfill."""
    client = await get_temporal_client()
    handle = await client.start_workflow(
        CaseAgentSessionBackfillWorkflow.run,
        id=_BACKFILL_WORKFLOW_ID,
        task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        execution_timeout=timedelta(days=1),
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    if handle.result_run_id is None:
        raise RuntimeError("Temporal did not return a backfill operation ID")
    return CaseAgentSessionInteractionBackfillStartResponse(
        operation_id=uuid.UUID(handle.result_run_id)
    )


@router.get(
    "/case-agent-session-interactions/backfill/{operation_id}",
    response_model=CaseAgentSessionInteractionBackfillStatusResponse,
)
async def get_case_agent_session_interaction_backfill(
    role: SuperuserRole,
    operation_id: uuid.UUID,
) -> CaseAgentSessionInteractionBackfillStatusResponse:
    """Poll a durable historical case-mutation backfill."""
    client = await get_temporal_client()
    handle = client.get_workflow_handle_for(
        CaseAgentSessionBackfillWorkflow.run,
        _BACKFILL_WORKFLOW_ID,
        run_id=str(operation_id),
    )
    try:
        description = await handle.describe()
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backfill operation not found",
        ) from exc

    if description.status in {
        WorkflowExecutionStatus.RUNNING,
        WorkflowExecutionStatus.CONTINUED_AS_NEW,
    }:
        operation_status = CaseAgentSessionBackfillStatus.RUNNING
    elif description.status == WorkflowExecutionStatus.COMPLETED:
        operation_status = CaseAgentSessionBackfillStatus.COMPLETED
    else:
        operation_status = CaseAgentSessionBackfillStatus.FAILED

    report = None
    if operation_status == CaseAgentSessionBackfillStatus.COMPLETED:
        result = await handle.result()
        report = CaseAgentSessionInteractionBackfillResponse.model_validate(
            result,
            from_attributes=True,
        )

    return CaseAgentSessionInteractionBackfillStatusResponse(
        operation_id=operation_id,
        status=operation_status,
        report=report,
    )
