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
from collections.abc import Mapping
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

    def json_loads(data: bytes | str) -> dict:  # type: ignore[misc]
        if isinstance(data, bytes):
            data = data.decode()
        return json.loads(data)

    def json_dumps(obj: dict) -> bytes:  # type: ignore[misc]
        return json.dumps(obj).encode()

    JSON_OUTPUT_IS_BYTES = True


def run_action_minimal(
    action_impl: dict[str, Any],
    args: Mapping[str, Any],
    secrets: dict[str, Any],
) -> Any:
    """Run an action with minimal imports.

    This is the core execution function for untrusted sandboxes.
    It does NOT import tracecat - only tracecat_registry.

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
            "Template actions are not yet supported in minimal runner. "
            "Use the full runner for template actions."
        )
    else:
        raise ValueError(f"Unknown action type: {impl_type}")


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

    # Set secrets as environment variables (same as env_sandbox)
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
                    result[f"{secret_name}__{k}".upper()] = str(v)
        elif secret_value is not None:
            result[secret_name.upper()] = str(secret_value)
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

        return {
            "success": False,
            "result": None,
            "error": {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc(),
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
