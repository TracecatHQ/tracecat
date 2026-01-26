"""Minimal action runner for sandboxed execution.

This module provides lightweight action execution for untrusted sandboxes.
It only imports tracecat_registry (NOT tracecat) to minimize cold start time.

Key design:
- No database access
- No SQLAlchemy, no heavy imports
- Action implementation metadata passed in via ResolvedContext
- Just import the module and call the function
"""

from __future__ import annotations

import asyncio
import importlib
import os
import warnings
from collections.abc import Mapping
from types import ModuleType
from typing import Any

# Only import what we absolutely need - no tracecat imports!
# Prefer orjson for performance (4-12x faster), fall back to stdlib json
try:
    import orjson

    def json_loads(data: bytes | str) -> dict:
        if isinstance(data, str):
            data = data.encode()
        return orjson.loads(data)

    def json_dumps(obj: dict) -> bytes:
        return orjson.dumps(obj)

    JSON_OUTPUT_IS_BYTES = True
except ImportError:
    import json

    def json_loads(data: bytes | str) -> dict:
        if isinstance(data, bytes):
            data = data.decode()
        return json.loads(data)

    def json_dumps(obj: dict) -> bytes:
        return json.dumps(obj).encode()

    JSON_OUTPUT_IS_BYTES = True


# Static config (read once at module load, immutable)
_API_URL = os.environ.get("TRACECAT__API_URL", "http://api:8000")


def run_action_minimal(
    action_impl: dict[str, Any],
    args: Mapping[str, Any],
    secrets: dict[str, Any],
) -> Any:
    """Run an action with minimal imports (sync, for subprocess execution).

    This is the core execution function for untrusted sandboxes.
    It does NOT import tracecat - only tracecat_registry.

    For warm workers with concurrent requests, use run_action_minimal_async() instead.

    Args:
        action_impl: Action implementation metadata with:
            - type: 'udf' or 'template'
            - module: Module path for UDF (e.g., 'tracecat_registry.integrations.core.transform')
            - name: Function name for UDF (e.g., 'reshape')
            - template_definition: Template definition for template actions
        args: Pre-evaluated arguments to pass to the action
        secrets: Pre-resolved secrets dict

    Returns:
        The action result

    Raises:
        ValueError: If action_impl is missing required fields
        NotImplementedError: If action type is not supported
    """
    impl_type = action_impl.get("type")

    if impl_type == "udf":
        return _run_udf(action_impl, args, secrets)
    elif impl_type == "template":
        raise NotImplementedError(
            "Template actions must be orchestrated at the service layer. "
            "The minimal runner should only receive UDF invocations."
        )
    else:
        raise ValueError(f"Unknown action type: {impl_type}")


async def run_action_minimal_async(
    action_impl: dict[str, Any],
    args: Mapping[str, Any],
    secrets: dict[str, Any],
    *,
    workspace_id: str,
    workflow_id: str,
    run_id: str,
    executor_token: str,
) -> Any:
    """Run an action asynchronously (for warm workers with concurrent requests).

    Unlike run_action_minimal(), this variant:
    - Uses await for async functions (proper event loop integration)
    - Uses asyncio.to_thread() for sync functions (doesn't block event loop)
    - Sets up RegistryContext from explicit params (not env vars)
    - Uses contextvars for task isolation with concurrent requests

    Args:
        action_impl: Action implementation metadata (type, module, name)
        args: Pre-evaluated arguments to pass to the action
        secrets: Pre-resolved secrets dict
        workspace_id: Workspace UUID for SDK context
        workflow_id: Workflow UUID for SDK context
        run_id: Run UUID for SDK context
        executor_token: JWT token for SDK authentication

    Returns:
        The action result
    """
    impl_type = action_impl.get("type")

    if impl_type == "udf":
        return await _run_udf_async(
            action_impl,
            args,
            secrets,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            run_id=run_id,
            executor_token=executor_token,
        )
    elif impl_type == "template":
        raise NotImplementedError(
            "Template actions must be orchestrated at the activity level. "
            "Backends should only receive UDF invocations."
        )
    else:
        raise ValueError(f"Unknown action type: {impl_type}")


async def _run_udf_async(
    action_impl: dict[str, Any],
    args: Mapping[str, Any],
    secrets: dict[str, Any],
    *,
    workspace_id: str,
    workflow_id: str,
    run_id: str,
    executor_token: str,
) -> Any:
    """Run a UDF action asynchronously with proper context setup."""
    module_path = action_impl.get("module")
    function_name = action_impl.get("name")

    if not module_path or not function_name:
        raise ValueError(
            f"UDF action missing module or name: module={module_path}, name={function_name}"
        )

    # Set up registry context from request payload (not env vars)
    # ContextVar ensures isolation between concurrent asyncio Tasks
    try:
        from tracecat_registry.context import RegistryContext, set_context

        registry_ctx = RegistryContext(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            run_id=run_id,
            api_url=_API_URL,  # Static, immutable
            token=executor_token,
        )
        set_context(registry_ctx)
    except ImportError:
        pass  # Registry context not available

    # Set secrets in registry secrets context (ContextVar - task-isolated)
    # NOTE: We use ContextVar instead of os.environ because os.environ is
    # process-global and not safe for concurrent async execution.
    secrets_token = None
    registry_secrets: ModuleType | None = None
    try:
        from tracecat_registry._internal import secrets as registry_secrets

        secrets_token = registry_secrets.set_context(_flatten_secrets(secrets))
    except ImportError:
        pass  # registry_secrets stays None
        warnings.warn(
            "Could not import tracecat_registry._internal.secrets - "
            "secrets may not be available to actions",
            RuntimeWarning,
            stacklevel=2,
        )

    try:
        # Import the module from tracecat_registry
        mod = importlib.import_module(module_path)
        fn = getattr(mod, function_name)

        # Use await for async, asyncio.to_thread for sync (doesn't block event loop)
        if asyncio.iscoroutinefunction(fn):
            return await fn(**args)
        else:
            return await asyncio.to_thread(fn, **args)
    finally:
        # Reset secrets context to prevent leakage between tasks
        if registry_secrets is not None and secrets_token is not None:
            registry_secrets.reset_context(secrets_token)


def _run_udf(
    action_impl: dict[str, Any],
    args: Mapping[str, Any],
    secrets: dict[str, Any],
) -> Any:
    """Run a UDF action by importing and calling the function."""
    module_path = action_impl.get("module")
    function_name = action_impl.get("name")

    if not module_path or not function_name:
        raise ValueError(
            f"UDF action missing module or name: module={module_path}, name={function_name}"
        )

    # Set up registry context from environment variables
    try:
        from tracecat_registry.context import RegistryContext, set_context

        registry_ctx = RegistryContext(
            workspace_id=os.environ.get("TRACECAT__WORKSPACE_ID", ""),
            workflow_id=os.environ.get("TRACECAT__WORKFLOW_ID", ""),
            run_id=os.environ.get("TRACECAT__RUN_ID", ""),
            api_url=_API_URL,
            token=os.environ.get("TRACECAT__EXECUTOR_TOKEN", ""),
        )
        set_context(registry_ctx)
    except ImportError:
        pass  # Registry context not available

    # Set secrets in registry secrets context (required for SDK-based actions)
    secrets_token = None
    registry_secrets: ModuleType | None = None
    try:
        from tracecat_registry._internal import secrets as registry_secrets

        secrets_token = registry_secrets.set_context(_flatten_secrets(secrets))
    except ImportError:
        warnings.warn(
            "Could not import tracecat_registry._internal.secrets - "
            "secrets may not be available to actions",
            RuntimeWarning,
            stacklevel=2,
        )

    # Also set env vars for backwards compatibility with actions that read env directly
    _set_env_secrets(secrets)

    try:
        # Import the module from tracecat_registry
        mod = importlib.import_module(module_path)
        fn = getattr(mod, function_name)

        # Check if async and run appropriately
        if asyncio.iscoroutinefunction(fn):
            return asyncio.run(fn(**args))
        else:
            return fn(**args)
    finally:
        # Reset secrets context
        if registry_secrets is not None and secrets_token is not None:
            registry_secrets.reset_context(secrets_token)
        # Clean up environment variables
        _clear_env_secrets(secrets)


def _set_env_secrets(secrets: dict[str, Any]) -> None:
    """Set flattened secrets as environment variables."""
    for key, value in _flatten_secrets(secrets).items():
        if value is not None:
            os.environ[key] = str(value)


def _clear_env_secrets(secrets: dict[str, Any]) -> None:
    """Remove secret environment variables."""
    for key in _flatten_secrets(secrets):
        os.environ.pop(key, None)


def _flatten_secrets(secrets: dict[str, Any]) -> dict[str, str]:
    """Flatten nested secrets dict to KEY__SUBKEY format."""
    result: dict[str, str] = {}
    for secret_name, secret_value in secrets.items():
        if isinstance(secret_value, dict):
            for k, v in secret_value.items():
                if v is not None:
                    if k in result:
                        raise ValueError(
                            f"Key {k!r} is duplicated in {secret_name!r}! "
                            "Please ensure only one secret with a given name is set."
                        )
                    result[k] = str(v)
        elif secret_value is not None:
            if secret_name in result:
                raise ValueError(
                    f"Key {secret_name!r} is duplicated! "
                    "Please ensure only one secret with a given name is set."
                )
            result[secret_name] = str(secret_value)
    return result


def main_minimal(input_data: dict[str, Any]) -> dict[str, Any]:
    """Main entry point for minimal runner.

    Args:
        input_data: Dict containing:
            - resolved_context: ResolvedContext with action_impl, secrets, evaluated_args
            - input: RunActionInput (for metadata only)

    Returns:
        Dict with 'success', 'result', and optionally 'error'
    """
    action_impl: dict[str, Any] | None = None
    try:
        # Extract what we need from resolved_context
        resolved_context = input_data.get("resolved_context", {})
        action_impl = resolved_context.get("action_impl")
        secrets = resolved_context.get("secrets", {})
        evaluated_args = resolved_context.get("evaluated_args", {})

        if not action_impl:
            raise ValueError("Missing action_impl in resolved_context")

        if evaluated_args is None:
            raise ValueError("Missing evaluated_args in resolved_context")

        # Run the action with pre-evaluated args
        result = run_action_minimal(action_impl, evaluated_args, secrets)

        return {"success": True, "result": result}

    except Exception as e:
        import traceback

        # Extract traceback info for ExecutorActionErrorInfo compatibility
        tb = traceback.extract_tb(e.__traceback__)
        last_frame = tb[-1] if tb else None

        # Build action name from impl if available
        action_name = "<unknown>"
        if action_impl:
            module = action_impl.get("module", "")
            name = action_impl.get("name", "")
            if module and name:
                action_name = f"{module}.{name}"
            elif name:
                action_name = name

        return {
            "success": False,
            "result": None,
            "error": {
                "type": type(e).__name__,
                "message": str(e),
                "action_name": action_name,
                "filename": last_frame.filename if last_frame else "<unknown>",
                "function": last_frame.name if last_frame else "<unknown>",
                "lineno": last_frame.lineno if last_frame else None,
            },
        }


if __name__ == "__main__":
    """Standalone entry point for subprocess execution."""
    import sys
    from pathlib import Path

    # Determine input source: file (sandbox) or stdin (direct)
    input_path = Path("/work/input.json")
    output_path = Path("/work/result.json")

    if input_path.exists():
        input_data = json_loads(input_path.read_bytes())
        use_file_io = True
    else:
        input_bytes = sys.stdin.buffer.read()
        input_data = json_loads(input_bytes)
        use_file_io = False

    # Run the action
    result = main_minimal(input_data)

    # Output result
    result_bytes = json_dumps(result)

    if use_file_io:
        output_path.write_bytes(result_bytes)
    else:
        sys.stdout.buffer.write(result_bytes)
        sys.stdout.buffer.flush()
