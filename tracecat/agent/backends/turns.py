"""Turn a prepared session turn into a child workflow its caller starts."""

from collections.abc import Sequence

from tracecat.agent.backends.registry import get_agent_backend, get_workflow_type
from tracecat.agent.backends.schemas import ChildAgentTurn
from tracecat.agent.backends.types import SessionWorkflowContext
from tracecat.agent.session.types import PreparedAgentTurn
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role


async def build_child_agent_turn(
    prepared: PreparedAgentTurn,
    *,
    role: Role,
    config: AgentConfig,
    tools: Sequence[str] | None,
    timeout_seconds: int | None = None,
    max_requests: int | None = None,
    max_tool_calls: int | None = None,
) -> ChildAgentTurn:
    """Build the session backend's workflow input and child start options.

    Args:
        prepared: The turn ``prepare_new_turn`` reserved.
        role: The role the turn executes as.
        config: The turn's configuration, which the caller may have narrowed.
        tools: The session's tools, which the caller may have narrowed.
        timeout_seconds: The caller's turn timeout, if it sets one.
        max_requests: The caller's request limit, if it sets one.
        max_tool_calls: The caller's tool call limit, if it sets one.
    """
    backend = get_agent_backend(prepared.backend_id, harness_type=prepared.harness_type)
    args = await backend.build_workflow_args(
        SessionWorkflowContext(
            session_id=prepared.session_id,
            backend_id=prepared.backend_id,
            harness_type=prepared.harness_type,
            title=prepared.title,
            entity_type=prepared.entity_type,
            entity_id=prepared.entity_id,
            tools=tuple(tools) if tools is not None else None,
            agent_preset_id=prepared.agent_preset_id,
            agent_preset_version_id=prepared.agent_preset_version_id,
            role=role,
            config=config,
            prompt=prepared.prompt,
            run_id=prepared.run_id,
            stream_id=prepared.active_stream_id,
            timeout_seconds=timeout_seconds,
            max_requests=max_requests,
            max_tool_calls=max_tool_calls,
        )
    )
    return ChildAgentTurn(
        backend_id=prepared.backend_id,
        session_id=prepared.session_id,
        run_id=prepared.run_id,
        workflow_type=get_workflow_type(backend),
        workflow_id=backend.workflow_id(prepared.run_id),
        task_queue=backend.task_queue,
        priority=backend.priority,
        retry_policy=backend.retry_policy,
        args=args,
    )
