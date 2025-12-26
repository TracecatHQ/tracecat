"""Untrusted action runner for sandboxed execution without DB access.

This module provides action execution for untrusted environments where
DB credentials are not available. It expects secrets and variables to
be pre-resolved and passed via RunActionInput.

Key differences from trusted runner (service.py):
- Does NOT access database directly
- Uses pre-resolved secrets from RunActionInput.resolved_secrets
- Uses pre-resolved variables from RunActionInput.resolved_variables
- Initializes SDK context for any SDK-based registry operations
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tracecat.contexts import ctx_logger, ctx_role, ctx_run, ctx_session_id
from tracecat.dsl.common import context_locator
from tracecat.dsl.graph import RunnableType
from tracecat.expressions.eval import evaluate_templated_args
from tracecat.expressions.shared import ExprContext
from tracecat.feature_flags.enums import FeatureFlag
from tracecat.feature_flags.service import is_feature_enabled
from tracecat.logger import logger
from tracecat.secrets import secrets_manager
from tracecat.types.common import apply_masks_object
from tracecat.types.generics import traverse_leaves

if TYPE_CHECKING:
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput


async def run_action_untrusted(input: RunActionInput, role: Role) -> Any:
    """Run an action in untrusted mode using pre-resolved secrets/variables.

    This function is similar to run_action_from_input but does NOT access the
    database. It expects secrets and variables to be pre-resolved and passed
    in the input object.

    Args:
        input: RunActionInput with resolved_secrets and resolved_variables set
        role: The Role for authorization context

    Returns:
        Action result

    Raises:
        ValueError: If required pre-resolved data is missing
    """
    # Set context variables
    ctx_role.set(role)
    ctx_run.set(input.run_context)
    ctx_session_id.set(input.session_id)

    # Initialize SDK context for any registry operations
    try:
        from tracecat_registry.context import RegistryContext, set_context
    except ImportError:
        RegistryContext = None
        set_context = None

    if RegistryContext is not None and set_context is not None:
        # Build context from environment variables (set by the sandbox)
        import os

        registry_ctx = RegistryContext(
            workspace_id=os.environ.get("TRACECAT__WORKSPACE_ID", ""),
            workflow_id=os.environ.get("TRACECAT__WORKFLOW_ID", ""),
            run_id=os.environ.get("TRACECAT__RUN_ID", ""),
            environment=os.environ.get("TRACECAT__ENVIRONMENT", "default"),
            api_url=os.environ.get("TRACECAT__API_URL", "http://api:8000"),
            token=os.environ.get("TRACECAT__EXECUTOR_TOKEN", ""),
        )
        set_context(registry_ctx)

    log = ctx_logger.get(logger.bind(ref=input.task.ref))
    task = input.task
    action_name = task.action

    # Get pre-resolved secrets and variables
    secrets = input.resolved_secrets or {}
    workspace_variables = input.resolved_variables or {}

    log.info(
        "Run action (untrusted mode)",
        task_ref=task.ref,
        action_name=action_name,
        has_secrets=bool(secrets),
        has_variables=bool(workspace_variables),
    )

    # Build masking set from secrets
    mask_values: set[str] | None = None
    for _, secret_value in traverse_leaves(secrets):
        if secret_value is not None:
            secret_str = str(secret_value)
            if len(secret_str) > 1:
                if mask_values is None:
                    mask_values = set()
                mask_values.add(secret_str)
            if isinstance(secret_value, str) and len(secret_value) > 1:
                if mask_values is None:
                    mask_values = set()
                mask_values.add(secret_value)

    # Build execution context
    context = input.exec_context.copy()
    context[ExprContext.SECRETS] = secrets
    context[ExprContext.VARS] = workspace_variables

    # Flatten secrets for env sandbox
    flattened_secrets = secrets_manager.flatten_secrets(secrets)

    # Initialize registry secrets context for SDK mode
    try:
        from tracecat_registry import registry_secrets

        if registry_secrets is not None and is_feature_enabled(
            FeatureFlag.REGISTRY_CLIENT
        ):
            registry_secrets.set_context(flattened_secrets)
    except ImportError:
        pass

    # Load the action from registry (this doesn't require DB for template actions)
    action = await _load_action_untrusted(action_name)

    with secrets_manager.env_sandbox(flattened_secrets):
        args = evaluate_templated_args(task, context)
        result = await _run_single_action(action=action, args=args, context=context)

    if mask_values:
        result = apply_masks_object(result, masks=mask_values)

    log.trace("Result", result=result)
    return result


async def _load_action_untrusted(action_name: str) -> Any:
    """Load an action from the registry without DB access.

    For template actions, this loads from the YAML templates.
    For UDFs, this loads from the registry package.
    """
    from tracecat.registry.actions.service import RegistryActionsService

    # Use a minimal service that doesn't require DB for action lookup
    # The action definition itself is in the registry package, not DB
    async with RegistryActionsService.with_session() as service:
        reg_action = await service.get_action(action_name)
        return service.get_bound(reg_action, mode="execution")


async def _run_single_action(
    action: Any,
    args: dict[str, Any],
    context: dict[str, Any],
) -> Any:
    """Execute a single action (template or UDF)."""
    from tracecat.contexts import ctx_interaction
    from tracecat.dsl.common import DSLRunArgs
    from tracecat.expressions.shared import ExprContext

    loc = context_locator(context)

    # Check action type
    runnable_type = getattr(action, "__tracecat_type__", None)

    if runnable_type == RunnableType.TEMPLATE:
        from tracecat.dsl.graph import RunnableGraph

        # Template action - run as subgraph
        dsl_args = DSLRunArgs(
            role=ctx_role.get(),
            dsl=action.definition,
            wf_id=loc.wf_id,
            trigger_inputs=args,
        )
        graph = RunnableGraph(dsl_args, parent_run_context=loc.run_context)
        result = await graph.run()

        # Extract result from the graph output
        if hasattr(result, "output"):
            return result.output
        return result

    else:
        # UDF - run directly
        # Check if this is an interactive action
        interaction_context = ctx_interaction.get(None)
        if interaction_context is not None:
            # Interactive UDF - needs special handling
            # For now, fall back to direct execution
            pass

        # Add local vars to context if present
        if ExprContext.LOCAL_VARS in context:
            args.update(context[ExprContext.LOCAL_VARS])

        # Execute the UDF
        result = await action.fn(**args)
        return result
