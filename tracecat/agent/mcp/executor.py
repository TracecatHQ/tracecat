"""Action executor for the trusted MCP server.

Uses ActionRunner with nsjail sandboxing for action execution.
To test locally, run in a Docker container with nsjail installed.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from tracecat.agent.tokens import MCPTokenClaims
from tracecat.auth.executor_tokens import mint_executor_token
from tracecat.contexts import ctx_role
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor.action_runner import get_action_runner
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorActionErrorInfo,
    ResolvedContext,
)
from tracecat.executor.service import (
    get_registry_artifacts_cached,
    get_workspace_variables,
)
from tracecat.expressions.common import ExprContext
from tracecat.expressions.eval import collect_expressions, eval_templated_object
from tracecat.identifiers import WorkflowUUID
from tracecat.identifiers.workflow import generate_exec_id
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionImplValidator
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets import secrets_manager


class ActionNotAllowedError(Exception):
    """Raised when an action is not in the allowed actions list."""


class ActionExecutionError(Exception):
    """Raised when action execution fails."""


async def execute_action(
    action_name: str,
    args: dict[str, Any],
    claims: MCPTokenClaims,
    timeout_seconds: int = 300,
) -> Any:
    """Execute an action using ActionRunner.

    Args:
        action_name: The action to execute (e.g., "tools.slack.post_message")
        args: Arguments to pass to the action
        claims: Token claims containing role and allowed_actions
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

    # Set role context
    ctx_role.set(claims.role)
    role = claims.role

    logger.info(
        "Executing action via ActionRunner",
        action_name=action_name,
        workspace_id=str(role.workspace_id),
        run_id=claims.run_id,
    )

    # Resolve context
    resolved_context = await _resolve_context(action_name, args, claims)

    # Build minimal RunActionInput
    run_input = _build_run_input(action_name, args)

    # Get tarball URI for registry environment
    tarball_uri = await _get_tarball_uri(role)

    # Execute via ActionRunner with nsjail sandbox
    runner = get_action_runner()
    result = await runner.execute_action(
        input=run_input,
        role=role,
        resolved_context=resolved_context,
        tarball_uris=[tarball_uri] if tarball_uri else None,
        timeout=float(timeout_seconds),
        force_sandbox=True,
    )

    # Handle result
    if isinstance(result, ExecutorActionErrorInfo):
        logger.error(
            "Action execution failed",
            action_name=action_name,
            error=str(result),
        )
        raise ActionExecutionError(str(result))

    logger.info(
        "Action executed successfully",
        action_name=action_name,
        workspace_id=str(role.workspace_id),
    )
    return result


async def _resolve_context(
    action_name: str,
    args: dict[str, Any],
    claims: MCPTokenClaims,
) -> ResolvedContext:
    """Resolve secrets, variables, and action implementation.

    Runs on the trusted side with DB access. The resulting ResolvedContext
    is passed to ActionRunner for subprocess execution.
    """
    role = claims.role

    # Get action implementation from DB
    async with RegistryActionsService.with_session() as service:
        reg_action = await service.get_action(action_name)
        action_secrets = await service.fetch_all_action_secrets(reg_action)

    # Build action implementation metadata
    impl = RegistryActionImplValidator.validate_python(reg_action.implementation)
    if impl.type == "template":
        action_impl = ActionImplementation(
            type="template",
            action_name=action_name,
            template_definition=impl.template_action.definition.model_dump(mode="json"),
        )
    else:
        action_impl = ActionImplementation(
            type="udf",
            action_name=action_name,
            module=impl.module,
            name=impl.name,
        )

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
        wf_id=claims.run_id or "mcp-execution",
        wf_exec_id=claims.session_id or "mcp-session",
    )

    return ResolvedContext(
        secrets=secrets,
        variables=workspace_variables,
        action_impl=action_impl,
        evaluated_args=dict(evaluated_args),
        workspace_id=str(role.workspace_id),
        workflow_id=claims.run_id or "mcp-execution",
        run_id=claims.session_id or "mcp-session",
        executor_token=executor_token,
    )


def _build_run_input(
    action_name: str,
    args: dict[str, Any],
) -> RunActionInput:
    """Build a minimal RunActionInput for ActionRunner."""
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
    )

    return RunActionInput(
        task=task,
        run_context=run_context,
        exec_context={},
    )


async def _get_tarball_uri(role) -> str | None:
    """Get the tarball URI for the current registry version.

    Works for both remote and local registries - both should have tarball_uri
    in the DB after sync.
    """

    try:
        artifacts = await get_registry_artifacts_cached(role)
        if artifacts:
            return artifacts[0].tarball_uri
    except Exception as e:
        logger.warning("Failed to get registry artifacts", error=str(e))

    return None
