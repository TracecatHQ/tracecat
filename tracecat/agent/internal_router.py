"""Internal router for agent execution (SDK/UDF use).

Serves ``tracecat_registry.sdk.agents.run_agent`` calls from in-sandbox
registry actions. Runs are dispatched to the durable agent workflow (the
same execution path as ``ai.agent``) and awaited to completion.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from temporalio.client import WorkflowFailureError
from temporalio.common import Priority

from tracecat import config
from tracecat.agent.schemas import (
    AgentOutput,
    InternalRunAgentRequest,
    RunAgentArgs,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.contexts import ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import RETRY_POLICIES
from tracecat.logger import logger
from tracecat.tiers.entitlements import Entitlement, check_entitlement

router = APIRouter(
    prefix="/internal/agent",
    tags=["internal-agent"],
    include_in_schema=False,
)


def _agent_config_from_request(params: InternalRunAgentRequest) -> AgentConfig | None:
    """Convert the request's config schema into a workflow AgentConfig."""
    if params.config is None:
        return None
    return AgentConfig(**params.config.model_dump())


def build_agent_workflow_args(
    params: InternalRunAgentRequest,
    *,
    role: Role,
    session_id: uuid.UUID,
):
    """Build DurableAgentWorkflow arguments for an internal SDK run."""
    from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs

    agent_args = RunAgentArgs(
        user_prompt=params.user_prompt,
        session_id=session_id,
        config=_agent_config_from_request(params),
        preset_slug=params.preset_slug,
        preset_version=params.preset_version,
        max_requests=params.max_requests,
        max_tool_calls=params.max_tool_calls,
    )
    return AgentWorkflowArgs(
        role=role,
        agent_args=agent_args,
        title="Agent run",
        # Workflow-initiated runs are hidden from chat lists; SDK runs have no
        # standalone entity, so the session anchors to itself.
        entity_type=AgentSessionEntity.WORKFLOW,
        entity_id=session_id,
    )


@router.post("/run", status_code=status.HTTP_200_OK)
@require_scope("agent:execute")
async def run_agent_endpoint(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: InternalRunAgentRequest,
) -> dict[str, object]:
    """Run an agent to completion via the durable agent workflow."""
    from tracecat_ee.agent.types import AgentWorkflowID
    from tracecat_ee.agent.workflows.durable import DurableAgentWorkflow

    ctx_role.set(role)

    if params.config is not None and params.config.tool_approvals:
        await check_entitlement(session, role, Entitlement.AGENT_ADDONS)

    session_id = uuid.uuid4()
    try:
        workflow_args = build_agent_workflow_args(
            params, role=role, session_id=session_id
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    client = await get_temporal_client()
    workflow_id = AgentWorkflowID(session_id)
    logger.info(
        "Dispatching internal agent run",
        workflow_id=str(workflow_id),
        session_id=str(session_id),
        preset_slug=params.preset_slug,
        task_queue=config.TRACECAT__AGENT_QUEUE,
    )
    try:
        result: AgentOutput = await client.execute_workflow(
            DurableAgentWorkflow.run,
            workflow_args,
            id=str(workflow_id),
            task_queue=config.TRACECAT__AGENT_QUEUE,
            retry_policy=RETRY_POLICIES["workflow:fail_fast"],
            priority=Priority(priority_key=1),
        )
    except WorkflowFailureError as e:
        logger.exception("Internal agent run failed", session_id=str(session_id))
        cause = e.cause or e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_type": type(cause).__name__,
                "message": str(cause),
            },
        ) from e
    return result.model_dump(mode="json")
