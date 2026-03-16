"""Temporal-backed registry UDF execution for trusted MCP calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError

from tracecat import config
from tracecat.agent.tokens import MCPTokenClaims
from tracecat.agent.workflows.tool_execution import (
    AGENT_TOOL_PRIORITY,
    ExecuteRegistryToolWorkflowInput,
)
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.identifiers import WorkflowUUID
from tracecat.identifiers.workflow import ExecutionUUID
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import (
    StoredObject,
    StoredObjectValidator,
    retrieve_stored_object,
)


class ActionNotAllowedError(Exception):
    """Raised when an action is not in the allowed actions list."""


class ActionExecutionError(Exception):
    """Raised when action execution fails."""


def build_role_from_claims(claims: MCPTokenClaims) -> Role:
    """Reconstruct the trusted service role from MCP token claims."""
    return Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=claims.workspace_id,
        organization_id=claims.organization_id,
        user_id=claims.user_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-mcp"],
    )


async def execute_action(
    action_name: str,
    args: dict[str, Any],
    claims: MCPTokenClaims,
    registry_lock: RegistryLock,
) -> Any:
    """Execute a registry UDF through the shared executor queue.

    Args:
        action_name: The action to execute (e.g., "tools.slack.post_message")
        args: Arguments to pass to the action
        claims: Token claims containing role and allowed_actions
        registry_lock: Registry lock with origin→version mappings for action resolution

    Returns:
        The action result

    Raises:
        ActionNotAllowedError: If action is not in allowed_actions
        ActionExecutionError: If execution fails
    """
    # Validate action is allowed
    if action_name not in claims.allowed_actions:
        logger.warning(
            "Action not allowed",
            action_name=action_name,
            allowed_actions=claims.allowed_actions,
        )
        raise ActionNotAllowedError(
            f"Action '{action_name}' is not in allowed actions for this token"
        )

    role = build_role_from_claims(claims)
    ctx_role.set(role)

    logger.info("Executing action via executor workflow", action_name=action_name)

    run_input = build_run_input(action_name, args, registry_lock)
    stored = await _execute_action_workflow(
        ExecuteRegistryToolWorkflowInput(role=role, run_input=run_input)
    )
    return await retrieve_stored_object(stored)


def build_run_input(
    action_name: str,
    args: dict[str, Any],
    registry_lock: RegistryLock,
    *,
    workflow_id: UUID | None = None,
    run_id: UUID | None = None,
    execution_id: UUID | None = None,
    logical_time: datetime | None = None,
    environment: str = "default",
) -> RunActionInput:
    """Build a minimal RunActionInput for ActionRunner.

    Args:
        action_name: The action to execute
        args: Arguments for the action
        registry_lock: Registry lock with origin→version mappings for action resolution
    """
    task = ActionStatement(
        ref=f"mcp_{action_name.replace('.', '_')}",
        action=action_name,
        args=args,
    )

    wf_id = WorkflowUUID.from_uuid(workflow_id or uuid4())
    run_context = RunContext(
        wf_id=wf_id,
        wf_run_id=run_id or uuid4(),
        wf_exec_id=f"{wf_id.short()}/{ExecutionUUID.from_uuid(execution_id or uuid4()).short()}",
        environment=environment,
        logical_time=logical_time or datetime.now(UTC),
    )

    return RunActionInput(
        task=task,
        run_context=run_context,
        exec_context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        registry_lock=registry_lock,
    )


async def _execute_action_workflow(
    input: ExecuteRegistryToolWorkflowInput,
) -> StoredObject:
    """Execute a single registry UDF via a short workflow on agent-worker."""
    from tracecat_ee.agent.workflows.registry_tool import ExecuteRegistryToolWorkflow

    client = await get_temporal_client()
    try:
        stored = await client.execute_workflow(
            ExecuteRegistryToolWorkflow.run,
            input,
            id=f"agent-tool-{uuid4()}",
            task_queue=config.TRACECAT__AGENT_QUEUE,
            run_timeout=timedelta(
                seconds=int(config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT) + 30
            ),
            priority=AGENT_TOOL_PRIORITY,
        )
    except WorkflowFailureError as e:
        cause = e.cause
        if isinstance(cause, ApplicationError):
            raise ActionExecutionError(str(cause)) from e
        raise ActionExecutionError(str(e)) from e
    return StoredObjectValidator.validate_python(stored)
