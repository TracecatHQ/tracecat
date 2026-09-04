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
import contextlib
import errno
import importlib
import os
import resource
import sys
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

    def _orjson_default(obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump(mode="json")
            except Exception as exc:
                if is_memory_exhaustion(exc):
                    # The address-space cap, not an unserializable type. Let it
                    # out so serialize_result can degrade to the resource-limit
                    # envelope instead of reporting a TypeError.
                    raise
        raise TypeError(f"Type is not JSON serializable: {type(obj).__name__}")

    def json_dumps(obj: dict) -> bytes:
        return orjson.dumps(obj, default=_orjson_default)

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
_ACTION_GATEWAY_SOCKET = os.environ.get("TRACECAT__ACTION_GATEWAY_SOCKET") or None
_CAPTURED_OUTPUT_CHAR_LIMIT = 8192
_SUPPRESSED_OUTPUT_PREVIEW_CHAR_LIMIT = 500


def _install_action_gateway_sdk_transport() -> None:
    """Patch legacy registry SDK clients to use the executor gateway socket."""
    if _ACTION_GATEWAY_SOCKET is None:
        return
    socket_path = _ACTION_GATEWAY_SOCKET

    try:
        import httpx

        sdk_client = importlib.import_module("tracecat_registry.sdk.client")
    except ImportError:
        return

    tracecat_client_cls = getattr(sdk_client, "TracecatClient", None)
    if tracecat_client_cls is None:
        return

    if hasattr(tracecat_client_cls, "_request_url_and_transport"):
        return

    if getattr(tracecat_client_cls, "_tracecat_action_gateway_transport", False):
        return

    async def request(
        self: Any,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_headers = self._get_headers()
        if headers:
            request_headers.update(headers)

        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=socket_path),
            timeout=getattr(self, "_timeout", 120.0),
        ) as client:
            response = await client.request(
                method,
                f"http://tracecat-action-gateway/internal{path}",
                params=params,
                json=json,
                headers=request_headers,
            )

        if not response.is_success:
            self._handle_error_response(response)

        if not response.content:
            return None

        return response.json()

    tracecat_client_cls.request = request
    tracecat_client_cls._tracecat_action_gateway_transport = True


class _CappedTextBuffer:
    """Lightweight text buffer with a hard character cap."""

    def __init__(self, limit: int) -> None:
        self._limit = max(limit, 0)
        self._size = 0
        self._parts: list[str] = []
        self.truncated = False

    def write(self, text: str) -> int:
        if not text:
            return 0

        text_len = len(text)
        remaining = self._limit - self._size
        if remaining > 0:
            chunk = text[:remaining]
            self._parts.append(chunk)
            self._size += len(chunk)

        if text_len > remaining:
            self.truncated = True

        return text_len

    def flush(self) -> None:
        return None

    def getvalue(self) -> str:
        return "".join(self._parts)


def _emit_suppressed_output_notice(
    *, stream_name: str, output: str, truncated: bool
) -> None:
    truncation_text = " (truncated)" if truncated else ""
    message = (
        f"Action emitted {stream_name} output that was suppressed"
        f"{truncation_text}: {output[:_SUPPRESSED_OUTPUT_PREVIEW_CHAR_LIMIT]}"
    )

    try:
        warnings.warn(message, RuntimeWarning, stacklevel=2)
    except Exception:
        try:
            sys.stderr.write(f"{message}\n")
            sys.stderr.flush()
        except Exception:
            return None


def run_action_minimal(
    action_impl: dict[str, Any],
    args: Mapping[str, Any],
    secret_env: dict[str, str],
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
        secret_env: Flat env-ready secret mapping

    Returns:
        The action result

    Raises:
        ValueError: If action_impl is missing required fields
        NotImplementedError: If action type is not supported
    """
    impl_type = action_impl.get("type")

    if impl_type == "udf":
        return _run_udf(action_impl, args, secret_env)
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
    secret_env: dict[str, str],
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
        secret_env: Flat env-ready secret mapping
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
            secret_env,
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
    secret_env: dict[str, str],
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

        _install_action_gateway_sdk_transport()
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

        secrets_token = registry_secrets.set_context(
            _normalize_secret_context(secret_env)
        )
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
    secret_env: dict[str, str],
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

        _install_action_gateway_sdk_transport()
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

        secrets_token = registry_secrets.set_context(
            _normalize_secret_context(secret_env)
        )
    except ImportError:
        warnings.warn(
            "Could not import tracecat_registry._internal.secrets - "
            "secrets may not be available to actions",
            RuntimeWarning,
            stacklevel=2,
        )

    # Also set env vars for backwards compatibility with actions that read env directly
    _set_env_secrets(secret_env)

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
        _clear_env_secrets(secret_env)


def _stringify_secret_env_value(key: str, value: Any) -> str | None:
    """Return a string env value or fail closed with key/type context."""
    if value is None:
        return None
    try:
        return str(value)
    except Exception as exc:
        raise TypeError(
            f"Failed to stringify secret env value for {key!r} ({type(value).__name__})"
        ) from exc


def _normalize_secret_context(secret_env: Mapping[str, Any]) -> dict[str, str]:
    """Normalize secrets for registry ContextVar consumers that expect strings."""
    normalized: dict[str, str] = {}
    for key, value in secret_env.items():
        if (secret_str := _stringify_secret_env_value(key, value)) is not None:
            normalized[key] = secret_str
    return normalized


def _set_env_secrets(secret_env: Mapping[str, Any]) -> None:
    """Set secret environment variables."""
    for key, value in secret_env.items():
        if (secret_str := _stringify_secret_env_value(key, value)) is not None:
            os.environ[key] = secret_str


def _clear_env_secrets(secret_env: Mapping[str, Any]) -> None:
    """Remove secret environment variables."""
    for key in secret_env:
        os.environ.pop(key, None)


def _collect_secret_mask_values(secret_env: Mapping[str, Any]) -> list[str]:
    """Normalize env values into mask candidates, failing closed on bad __str__."""
    mask_values: set[str] = set()
    for key, value in secret_env.items():
        if (secret_str := _stringify_secret_env_value(key, value)) is None:
            continue
        if len(secret_str) > 1:
            mask_values.add(secret_str)
    return sorted(mask_values, key=len, reverse=True)


def main_minimal(input_data: dict[str, Any]) -> dict[str, Any]:
    """Main entry point for minimal runner.

    Args:
        input_data: Dict containing:
            - resolved_context: ResolvedContext with action_impl and evaluated_args
            - secret_env: Flat env-ready secret mapping
            - input: RunActionInput (for metadata only)

    Returns:
        Dict with 'success', 'result', and optionally 'error'
    """
    action_impl: dict[str, Any] | None = None
    try:
        # Extract what we need from resolved_context
        resolved_context = input_data.get("resolved_context", {})
        action_impl = resolved_context.get("action_impl")
        secret_env = input_data.get("secret_env", {})
        evaluated_args = resolved_context.get("evaluated_args", {})

        if not action_impl:
            raise ValueError("Missing action_impl in resolved_context")

        if evaluated_args is None:
            raise ValueError("Missing evaluated_args in resolved_context")

        # Run the action with pre-evaluated args.
        # Capture action-level stdout/stderr to prevent protocol corruption.
        # The direct executor parses this process stdout as JSON bytes.
        action_stdout = _CappedTextBuffer(_CAPTURED_OUTPUT_CHAR_LIMIT)
        action_stderr = _CappedTextBuffer(_CAPTURED_OUTPUT_CHAR_LIMIT)
        with (
            contextlib.redirect_stdout(action_stdout),
            contextlib.redirect_stderr(action_stderr),
        ):
            result = run_action_minimal(action_impl, evaluated_args, secret_env)

        # Mask secret values in captured output to prevent leaking credentials
        mask_values = _collect_secret_mask_values(secret_env)
        if captured_stdout := action_stdout.getvalue().strip():
            for mask in mask_values:
                captured_stdout = captured_stdout.replace(mask, "***")
            _emit_suppressed_output_notice(
                stream_name="stdout",
                output=captured_stdout,
                truncated=action_stdout.truncated,
            )
        if captured_stderr := action_stderr.getvalue().strip():
            for mask in mask_values:
                captured_stderr = captured_stderr.replace(mask, "***")
            _emit_suppressed_output_notice(
                stream_name="stderr",
                output=captured_stderr,
                truncated=action_stderr.truncated,
            )

        return {"success": True, "result": result}

    except Exception as e:
        if is_memory_exhaustion(e):
            # Skip the traceback walk, which needs memory the process may not
            # have, and report a structured code so the host classifies the
            # failure without inspecting error text.
            return _resource_limit_envelope(
                "Action ran out of memory",
                action_name=_action_display_name(action_impl),
            )

        import traceback

        # Extract traceback info for ExecutorActionErrorInfo compatibility
        tb = traceback.extract_tb(e.__traceback__)
        last_frame = tb[-1] if tb else None

        return {
            "success": False,
            "result": None,
            "error": {
                "type": type(e).__name__,
                "message": str(e),
                "action_name": _action_display_name(action_impl),
                "filename": last_frame.filename if last_frame else "<unknown>",
                "function": last_frame.name if last_frame else "<unknown>",
                "lineno": last_frame.lineno if last_frame else None,
            },
        }


def is_memory_exhaustion(error: BaseException) -> bool:
    """Report whether an exception means the address-space cap was reached.

    Python's own allocator raises ``MemoryError``, but a syscall that fails
    under ``rlimit_as`` -- ``mmap`` most commonly -- surfaces as ``OSError``
    with ``errno.ENOMEM`` instead. Both mean the same cap, and both are matched
    on a machine-readable attribute rather than on message text.
    """
    if isinstance(error, MemoryError):
        return True
    return isinstance(error, OSError) and error.errno == errno.ENOMEM


def caused_by_memory_exhaustion(error: BaseException) -> bool:
    """Report whether memory exhaustion produced ``error``, directly or as a cause.

    orjson reports an exception raised inside its ``default`` hook as its own
    ``JSONEncodeError`` carrying the original on ``__cause__``, so a model whose
    ``model_dump()`` hits the cap arrives here one level down rather than as
    itself.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if is_memory_exhaustion(current):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def serialize_result(result: dict[str, Any], input_data: dict[str, Any]) -> bytes:
    """Serialize the runner result, degrading to a fixed envelope on MemoryError.

    An action whose return value fits under the address-space cap can still
    exhaust it while being serialized, and that ``MemoryError`` is raised after
    ``main_minimal`` has already returned. Without this the process would die
    before writing ``result.json`` and the failure would degrade to a generic
    workload error, losing the resource-limit code.
    """
    try:
        return json_dumps(result)
    except Exception as exc:
        if not caused_by_memory_exhaustion(exc):
            raise
        # Drop the oversized result, and the traceback pinning it through the
        # serializer's frame locals, before allocating anything else.
        exc.__traceback__ = None
        result.clear()
        return json_dumps(
            _resource_limit_envelope(
                "Action ran out of memory while serializing its result",
                action_name=_action_display_name(
                    input_data.get("resolved_context", {}).get("action_impl")
                ),
            )
        )


def _resource_limit_envelope(message: str, *, action_name: str) -> dict[str, Any]:
    """Build the structured envelope reported when the memory cap is hit.

    Allocates only small, fixed-size values so it stays usable in a process
    that has already exhausted its address space.

    The message stays neutral about where the memory ran out. This runner also
    executes unjailed, under the default direct backend, where no address-space
    rlimit exists; the host names the cap only on the sandboxed path, from
    ``sandbox_resource_limit_message()``, and the direct path drops
    ``error_code`` and surfaces this message as an ordinary action failure.
    """
    return {
        "success": False,
        "result": None,
        "error": {
            "type": "MemoryError",
            "message": message,
            "action_name": action_name,
            "filename": "<sandbox>",
            "function": "run_action_minimal",
            "lineno": None,
        },
        "error_code": "resource_limit_exceeded",
    }


def _action_display_name(action_impl: dict[str, Any] | None) -> str:
    """Build the action name reported in a structured runner error."""
    if not action_impl:
        return "<unknown>"
    module = action_impl.get("module", "")
    name = action_impl.get("name", "")
    if module and name:
        return f"{module}.{name}"
    if name:
        return name
    return "<unknown>"


def _enforce_nproc_limit() -> None:
    """Cap the sandbox's process count via RLIMIT_NPROC.

    nsjail cannot enforce rlimit_nproc when it creates a user namespace
    (clone_newuser), so the host injects the configured limit via
    TRACECAT__SANDBOX_RLIMIT_NPROC and this trusted runner applies it before
    any untrusted code runs. Lowering a hard rlimit is permitted for
    unprivileged processes, and the limit is enforced per real UID, which
    covers every process in the sandbox.
    """
    raw = os.environ.get("TRACECAT__SANDBOX_RLIMIT_NPROC")
    if not raw:
        return
    try:
        limit = int(raw)
        if limit <= 0:
            raise ValueError(f"non-positive cap: {limit}")
        _soft, current_hard = resource.getrlimit(resource.RLIMIT_NPROC)
        finite = current_hard != resource.RLIM_INFINITY
        # Already capped when the inherited hard cap is at or below the
        # requested limit (including 0 = no child processes), or when a
        # stricter finite soft limit is in force: raising either would only
        # relax enforcement.
        already_capped = 0 <= current_hard <= limit if finite else 0 < _soft <= limit
        if not already_capped:
            resource.setrlimit(resource.RLIMIT_NPROC, (limit, limit))
    except (ValueError, OSError, OverflowError) as exc:
        # Values are host-injected; a malformed value or an unenforceable
        # rlimit means the process cap is not in place. Exit instead of
        # running untrusted code without the enforced cap.
        raise SystemExit(
            f"_enforce_nproc_limit: could not enforce RLIMIT_NPROC={raw!r}: "
            f"{type(exc).__name__}"
        ) from exc


if __name__ == "__main__":
    """Standalone entry point for subprocess execution."""
    import sys
    from pathlib import Path

    _enforce_nproc_limit()

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
    result_bytes = serialize_result(result, input_data)

    if use_file_io:
        output_path.write_bytes(result_bytes)
    else:
        sys.stdout.buffer.write(result_bytes)
        sys.stdout.buffer.flush()
