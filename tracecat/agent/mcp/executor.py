"""Action executor for the trusted MCP server.

Uses ActionRunner with nsjail sandboxing for action execution.
To test locally, run in a Docker container with nsjail installed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from tracecat.agent.tokens import MCPTokenClaims
from tracecat.auth.executor_tokens import mint_executor_token
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.executor.backends.ephemeral import EphemeralBackend
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorResultFailure,
    ResolvedContext,
)
from tracecat.executor.service import (
    get_workspace_variables,
)
from tracecat.expressions.common import ExprContext
from tracecat.expressions.eval import collect_expressions, eval_templated_object
from tracecat.identifiers import WorkflowUUID
from tracecat.identifiers.workflow import generate_exec_id
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionImplValidator
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.lock.types import RegistryLock
from tracecat.secrets import secrets_manager


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
    timeout_seconds: int = 300,
) -> Any:
    """Execute an action using ActionRunner.

    Args:
        action_name: The action to execute (e.g., "tools.slack.post_message")
        args: Arguments to pass to the action
        claims: Token claims containing role and allowed_actions
        registry_lock: Registry lock with origin→version mappings for action resolution
        timeout_seconds: Maximum execution time

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
        user_id=claims.user_id,
    )
    ctx_role.set(role)

    logger.info(
        "Executing action via ActionRunner",
    )

    # Resolve context
    resolved_context = await _resolve_context(action_name, args, claims)

    # Build minimal RunActionInput
    run_input = _build_run_input(action_name, args, registry_lock)

    # Execute via ActionRunner with nsjail sandbox
    backend = EphemeralBackend()
    execution = await backend.execute(
        input=run_input,
        role=role,
        resolved_context=resolved_context,
        timeout=float(timeout_seconds),
    )

    # Handle result
    if isinstance(execution, ExecutorResultFailure):
        raise ActionExecutionError(execution.error.message)
    return execution.result


async def _resolve_context(
    action_name: str,
    args: dict[str, Any],
    claims: MCPTokenClaims,
) -> ResolvedContext:
    """Resolve secrets, variables, and action implementation.

    Runs on the trusted side with DB access. The resulting ResolvedContext
    is passed to ActionRunner for subprocess execution.
    """
    # Reconstruct Role from claims (same as in execute_action)
    role = Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=claims.workspace_id,
        user_id=claims.user_id,
    )

    # Get action from index + manifest (not RegistryAction table)
    async with RegistryActionsService.with_session(role=role) as service:
        indexed_result = await service.get_action_from_index(action_name)
        if indexed_result is None:
            raise ActionNotFoundError(f"Action '{action_name}' not found in registry")

    # Get manifest action and aggregate secrets from manifest
    manifest_action = indexed_result.manifest.actions.get(action_name)
    if manifest_action is None:
        raise ActionNotFoundError(f"Action '{action_name}' not found in manifest")

    action_secrets = set(
        RegistryActionsService.aggregate_secrets_from_manifest(
            indexed_result.manifest, action_name
        )
    )

    # Build action implementation metadata from manifest
    impl = RegistryActionImplValidator.validate_python(manifest_action.implementation)
    if impl.type == "template":
        action_impl = ActionImplementation(
            type="template",
            action_name=action_name,
            template_definition=impl.template_action.definition.model_dump(mode="json"),
        )
    elif impl.type == "udf":
        action_impl = ActionImplementation(
            type="udf",
            action_name=action_name,
            module=impl.module,
            name=impl.name,
        )
    else:
        raise ValueError(f"Unknown implementation type: {impl}")

    # Collect expressions to know what secrets/variables are needed
    collected = collect_expressions(args)

    # Fetch secrets and variables
    secrets = await secrets_manager.get_action_secrets(
        secret_exprs=collected.secrets, action_secrets=action_secrets
    )
    workspace_variables = await get_workspace_variables(
        variable_exprs=collected.variables,
        role=role,
    )

    # Build execution context for expression evaluation
    context = {
        ExprContext.SECRETS: secrets,
        ExprContext.VARS: workspace_variables,
    }

    # Evaluate templated args
    evaluated_args = eval_templated_object(args, operand=context)

    # Generate executor token for SDK authentication
    if role.workspace_id is None:
        raise ValueError("workspace_id is required for action execution")
    executor_token = mint_executor_token(
        workspace_id=role.workspace_id,
        user_id=role.user_id,
        wf_id="wf_agent",
        wf_exec_id="wf_agent_exec",
    )

    return ResolvedContext(
        secrets=secrets,
        variables=workspace_variables,
        action_impl=action_impl,
        evaluated_args=dict(evaluated_args),
        workspace_id=str(role.workspace_id),
        workflow_id="wf_agent",
        run_id="wf_agent_run",
        executor_token=executor_token,
    )


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
