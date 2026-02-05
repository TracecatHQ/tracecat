"""Action executor for the trusted MCP server.

Uses ActionRunner with nsjail sandboxing for action execution.
To test locally, run in a Docker container with nsjail installed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from tracecat.agent.common.config import TRACECAT__DISABLE_NSJAIL
from tracecat.agent.tokens import MCPTokenClaims
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.executor.backends.direct import DirectBackend
from tracecat.executor.backends.ephemeral import EphemeralBackend
from tracecat.executor.service import dispatch_action
from tracecat.identifiers import WorkflowUUID
from tracecat.identifiers.workflow import generate_exec_id
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock


class ActionNotFoundError(Exception):
    """Raised when an action is not found in the registry."""


class ActionNotAllowedError(Exception):
    """Raised when an action is not in the allowed actions list."""


class ActionExecutionError(Exception):
    """Raised when action execution fails."""


async def execute_action(
    action_name: str,
    args: dict[str, Any],
    claims: MCPTokenClaims,
    registry_lock: RegistryLock,
) -> Any:
    """Execute an action using dispatch_action.

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

    # Reconstruct Role from token claims on the trusted side
    role = Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=claims.workspace_id,
        organization_id=claims.organization_id,
        user_id=claims.user_id,
    )
    ctx_role.set(role)

    logger.info(
        "Executing action via ActionRunner",
    )

    # Build minimal RunActionInput
    run_input = _build_run_input(action_name, args, registry_lock)

    if TRACECAT__DISABLE_NSJAIL:
        backend = DirectBackend()
    else:
        backend = EphemeralBackend()
    # Execute via ActionRunner with nsjail sandbox
    return await dispatch_action(backend, run_input)


def _build_run_input(
    action_name: str,
    args: dict[str, Any],
    registry_lock: RegistryLock,
) -> RunActionInput:
    """Build a minimal RunActionInput for ActionRunner.

    Args:
        action_name: The action to execute
        args: Arguments for the action
        registry_lock: Registry lock with origin→version mappings for action resolution
    """
    # Create minimal action statement
    task = ActionStatement(
        ref=f"mcp_{action_name.replace('.', '_')}",
        action=action_name,
        args=args,
    )

    # Create minimal run context with properly formatted IDs
    wf_id = WorkflowUUID.new_uuid4()
    run_context = RunContext(
        wf_id=wf_id,
        wf_run_id=uuid4(),
        wf_exec_id=generate_exec_id(wf_id),
        environment="default",
        logical_time=datetime.now(UTC),
    )

    return RunActionInput(
        task=task,
        run_context=run_context,
        exec_context=ExecutionContext(ACTIONS={}, TRIGGER=None),
        registry_lock=registry_lock,
    )
