"""Untrusted action runner for sandboxed execution without DB access.

This module provides action execution for untrusted environments where
DB credentials are not available. It expects secrets and variables to
be pre-resolved and passed separately via ResolvedContext.

Key differences from trusted runner (service.py):
- Does NOT access database directly
- Uses pre-resolved secrets from ResolvedContext
- Uses pre-resolved variables from ResolvedContext
- Initializes SDK context for any SDK-based registry operations
"""

from __future__ import annotations

import os
from typing import Any

from tracecat.auth.types import Role
from tracecat.contexts import (
    ctx_interaction,
    ctx_logger,
    ctx_role,
    ctx_run,
    ctx_session_id,
)
from tracecat.dsl.schemas import ExecutionContext, RunActionInput
from tracecat.executor.schemas import ResolvedContext
from tracecat.executor.service import evaluate_templated_args, run_single_action
from tracecat.expressions.common import ExprContext
from tracecat.feature_flags import is_feature_enabled
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.logger import logger
from tracecat.parse import traverse_leaves
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets import secrets_manager
from tracecat.secrets.common import apply_masks_object

# Optional imports for registry SDK
try:
    from tracecat_registry import secrets as registry_secrets
    from tracecat_registry.context import RegistryContext, set_context
except ImportError:
    registry_secrets = None  # type: ignore[assignment]
    RegistryContext = None  # type: ignore[assignment, misc]
    set_context = None  # type: ignore[assignment]


async def run_action_untrusted(
    input: RunActionInput,
    role: Role,
    resolved_context: ResolvedContext,
) -> Any:
    """Run an action in untrusted mode using pre-resolved secrets/variables.

    This function is similar to run_action_from_input but does NOT access the
    database. It expects secrets and variables to be pre-resolved and passed
    via the resolved_context parameter.

    Args:
        input: RunActionInput with task and execution context
        role: The Role for authorization context
        resolved_context: Pre-resolved secrets and variables

    Returns:
        Action result

    Raises:
        Exception: If action execution fails
    """
    # Set context variables
    ctx_role.set(role)
    ctx_run.set(input.run_context)
    ctx_session_id.set(input.session_id)
    # Always set interaction context (even if None) to prevent stale context leakage
    ctx_interaction.set(input.interaction_context)

    # Initialize SDK context for any registry operations
    _setup_registry_sdk_context()

    log = ctx_logger.get(logger.bind(ref=input.task.ref))
    task = input.task
    action_name = task.action

    # Get pre-resolved secrets and variables from resolved_context
    secrets = resolved_context.secrets
    workspace_variables = resolved_context.variables

    log.info(
        "Run action (untrusted mode)",
        task_ref=task.ref,
        action_name=action_name,
        has_secrets=bool(secrets),
        has_variables=bool(workspace_variables),
    )

    # Build masking set from secrets
    mask_values: set[str] = set()
    for _, secret_value in traverse_leaves(secrets):
        if secret_value is not None:
            secret_str = str(secret_value)
            # Only mask non-empty string values that are longer than 1 character
            if len(secret_str) > 1:
                mask_values.add(secret_str)
            if isinstance(secret_value, str) and len(secret_value) > 1:
                mask_values.add(secret_value)

    # Build execution context with pre-resolved values
    context: ExecutionContext = input.exec_context.copy()
    context[ExprContext.SECRETS] = secrets
    context[ExprContext.VARS] = workspace_variables

    # Flatten secrets for env sandbox
    flattened_secrets = secrets_manager.flatten_secrets(secrets)

    # Initialize registry secrets context for SDK mode
    _setup_registry_secrets_context(flattened_secrets)

    # Load the action from registry
    async with RegistryActionsService.with_session() as service:
        reg_action = await service.get_action(action_name)
        action = service.get_bound(reg_action, mode="execution")

    # Execute with secrets in environment
    with secrets_manager.env_sandbox(flattened_secrets):
        args = evaluate_templated_args(task, context)
        result = await run_single_action(action=action, args=args, context=context)

    # Apply masking
    if mask_values:
        result = apply_masks_object(result, masks=mask_values)

    log.trace("Result", result=result)
    return result


def _setup_registry_sdk_context() -> None:
    """Set up registry SDK context from environment variables."""
    if RegistryContext is None or set_context is None:
        return

    registry_ctx = RegistryContext(
        workspace_id=os.environ.get("TRACECAT__WORKSPACE_ID", ""),
        workflow_id=os.environ.get("TRACECAT__WORKFLOW_ID", ""),
        run_id=os.environ.get("TRACECAT__RUN_ID", ""),
        environment=os.environ.get("TRACECAT__ENVIRONMENT", "default"),
        api_url=os.environ.get("TRACECAT__API_URL", "http://api:8000"),
        token=os.environ.get("TRACECAT__EXECUTOR_TOKEN", ""),
    )
    set_context(registry_ctx)


def _setup_registry_secrets_context(flattened_secrets: dict[str, str]) -> None:
    """Set up registry secrets context for SDK mode."""
    if registry_secrets is not None and is_feature_enabled(FeatureFlag.REGISTRY_CLIENT):
        registry_secrets.set_context(flattened_secrets)
