"""Untrusted action runner for sandboxed execution without DB access.

This module provides action execution for untrusted environments where
DB credentials are not available. It expects secrets, variables, and
action implementation to be pre-resolved and passed via ResolvedContext.

Key differences from trusted runner (service.py):
- Does NOT access database directly
- Uses pre-resolved secrets from ResolvedContext
- Uses pre-resolved variables from ResolvedContext
- Uses pre-resolved action_impl from ResolvedContext (no registry DB lookup)
- Uses pre-resolved evaluated_args from ResolvedContext
- Initializes SDK context for any SDK-based registry operations
"""

from __future__ import annotations

import asyncio
import importlib
import os
from typing import Any

from tracecat_registry import secrets as registry_secrets
from tracecat_registry.context import RegistryContext, set_context

from tracecat.auth.types import Role
from tracecat.contexts import (
    ctx_interaction,
    ctx_logger,
    ctx_role,
    ctx_run,
    ctx_session_id,
)
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor.schemas import ResolvedContext
from tracecat.logger import logger
from tracecat.parse import traverse_leaves
from tracecat.secrets import secrets_manager
from tracecat.secrets.common import apply_masks_object


async def run_action_untrusted(
    input: RunActionInput,
    role: Role,
    resolved_context: ResolvedContext,
) -> Any:
    """Run an action in untrusted mode using pre-resolved context.

    This function does NOT access the database. It expects everything
    needed for execution to be pre-resolved and passed via resolved_context:
    - secrets: Pre-resolved secrets dict
    - variables: Pre-resolved workspace variables
    - action_impl: Action implementation metadata (module, name)
    - evaluated_args: Pre-evaluated action arguments

    Args:
        input: RunActionInput with task and execution context
        role: The Role for authorization context
        resolved_context: Pre-resolved execution context

    Returns:
        Action result

    Raises:
        ValueError: If action_impl or evaluated_args are missing
        NotImplementedError: If action type is 'template' (templates must be
            orchestrated at the activity level, not inside the sandbox)
        Exception: If action execution fails
    """
    # Validate required fields
    if resolved_context.action_impl is None:
        raise ValueError("Missing action_impl in resolved_context")
    if resolved_context.evaluated_args is None:
        raise ValueError("Missing evaluated_args in resolved_context")

    action_impl = resolved_context.action_impl
    evaluated_args = resolved_context.evaluated_args

    # Template actions should be orchestrated at the activity level,
    # not inside the sandbox. Each template step should be dispatched
    # as a separate UDF invocation.
    if action_impl.type == "template":
        raise NotImplementedError(
            "Template actions must be orchestrated at the activity level. "
            "Backends should only receive UDF invocations."
        )

    # Set context variables
    ctx_role.set(role)
    ctx_run.set(input.run_context)
    ctx_session_id.set(input.session_id)
    # Always set interaction context (even if None) to prevent stale context leakage
    ctx_interaction.set(input.interaction_context)

    # Initialize SDK context for any registry operations
    _setup_registry_sdk_context()

    log = ctx_logger.get() or logger.bind(ref=input.task.ref)
    task = input.task
    action_name = task.action

    # Get pre-resolved secrets from resolved_context
    secrets = resolved_context.secrets

    log.info(
        "Run action (untrusted mode)",
        task_ref=task.ref,
        action_name=action_name,
        action_type=action_impl.type,
        action_module=action_impl.module,
        action_func=action_impl.name,
        has_secrets=bool(secrets),
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

    # Flatten secrets for env sandbox
    flattened_secrets = secrets_manager.flatten_secrets(secrets)

    # Initialize registry secrets context for SDK mode
    secrets_token = registry_secrets.set_context(flattened_secrets)

    try:
        # Execute the UDF directly (no DB lookup needed)
        with secrets_manager.env_sandbox(flattened_secrets):
            result = await _run_udf(
                action_impl.module, action_impl.name, evaluated_args
            )

        # Apply masking
        if mask_values:
            result = apply_masks_object(result, masks=mask_values)

        log.trace("Result", result=result)
        return result
    finally:
        # Reset secrets context to prevent leakage
        registry_secrets.reset_context(secrets_token)


async def _run_udf(
    module_path: str | None,
    function_name: str | None,
    args: dict[str, Any],
) -> Any:
    """Run a UDF action by importing and calling the function.

    Args:
        module_path: Full module path (e.g., 'tracecat_registry.integrations.core.transform')
        function_name: Function name (e.g., 'reshape')
        args: Pre-evaluated arguments to pass to the function

    Returns:
        The function result

    Raises:
        ValueError: If module_path or function_name is missing
    """
    if not module_path or not function_name:
        raise ValueError(
            f"UDF action missing module or name: module={module_path}, name={function_name}"
        )

    # Import the module and get the function
    mod = importlib.import_module(module_path)
    fn = getattr(mod, function_name)

    # Check if async and run appropriately
    if asyncio.iscoroutinefunction(fn):
        return await fn(**args)
    else:
        return await asyncio.to_thread(fn, **args)


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
