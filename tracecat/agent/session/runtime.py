"""API-side contract for the separately deployed Pi runtime worker."""

import uuid

from pydantic import BaseModel, Field, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy

from tracecat import config
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_config import agent_config_to_payload
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession, AgentSessionHistory
from tracecat.dsl.client import get_temporal_client
from tracecat.exceptions import TracecatConflictError
from tracecat.feature_flags.enums import FeatureFlag


class RuntimeTurnPayload(BaseModel):
    """JSON contract accepted by AgentTurnWorkflow without importing its worker."""

    turn_id: uuid.UUID
    session_id: uuid.UUID
    active_stream_id: uuid.UUID
    role: Role
    config: AgentConfigPayload
    prompt: str = Field(min_length=1, max_length=100_000)
    target: dict[str, JsonValue] | None = None


def runtime_workflow_id(turn_id: uuid.UUID) -> str:
    return f"agent-turn-{turn_id}"


def session_workflow_id(session: AgentSession) -> str:
    """Resolve the workflow identity from the session's immutable backend."""
    if session.harness_type == "pi_rpc":
        assert session.curr_run_id is not None
        return runtime_workflow_id(session.curr_run_id)
    return f"agent/{session.curr_run_id}"


async def start_runtime_turn(
    db: AsyncSession,
    session: AgentSession,
    *,
    role: Role,
    agent_config: AgentConfig,
    prompt: str,
    run_id: uuid.UUID,
    stream_id: uuid.UUID,
) -> None:
    """Reserve one session and dispatch to the runtime's dedicated queue."""
    if FeatureFlag.AGENT_RUNTIME not in config.TRACECAT__FEATURE_FLAGS:
        raise ValueError("Pi backend is not enabled on this deployment")
    payload = agent_config_to_payload(agent_config)
    if payload.enable_internet_access or payload.agents.subagents:
        raise ValueError("Pi does not yet support internet access or subagents")
    if any(server.type == "stdio" for server in payload.mcp_servers or []):
        raise ValueError("Pi does not yet support stdio MCP servers")
    # Claude's output-validation retry loop has no equivalent in Pi. Pi owns
    # its native structured-output loop; do not request a second host loop.
    payload = payload.model_copy(update={"retries": 0})
    locked = await db.scalar(
        select(AgentSession)
        .where(
            AgentSession.id == session.id,
            AgentSession.workspace_id == role.workspace_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or locked.curr_run_id is not None:
        raise TracecatConflictError("This chat already has an active turn")
    binding = await db.scalar(
        select(AgentSessionHistory.content).where(
            AgentSessionHistory.session_id == session.id,
            AgentSessionHistory.kind == "pi-binding",
        )
    )
    turn = RuntimeTurnPayload(
        turn_id=run_id,
        session_id=session.id,
        active_stream_id=stream_id,
        role=role,
        config=payload,
        prompt=prompt,
        target=binding,
    )
    locked.curr_run_id = run_id
    locked.active_stream_id = stream_id
    locked.last_error = None
    # Preserve the user's bubble during cold startup and stream reconnects.
    # The display adapter replaces it with Pi's native user entry at checkpoint.
    db.add(
        AgentSessionHistory(
            id=uuid.uuid5(session.id, f"pi-input:{run_id}"),
            workspace_id=session.workspace_id,
            session_id=session.id,
            curr_run_id=run_id,
            kind="pi-input",
            content={
                "type": "user",
                "message": {
                    "type": "user",
                    "content": [{"type": "text", "text": prompt}],
                },
            },
        )
    )
    await db.commit()
    client = await get_temporal_client()
    # Keep the reservation on an ambiguous start failure: another message must
    # never execute over a workflow that may already have started.
    await client.start_workflow(
        "AgentTurnWorkflow",
        turn,
        id=runtime_workflow_id(run_id),
        task_queue=config.TRACECAT__AGENT_RUNTIME_QUEUE,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        retry_policy=RetryPolicy(maximum_attempts=1),
    )
