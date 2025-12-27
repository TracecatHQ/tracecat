"""Subprocess entrypoint for sandboxed action execution.

This module is invoked by nsjail or direct subprocess execution to run
registry actions in isolation. It supports two modes:

Trusted mode (default):
- Has access to DB credentials via environment variables
- Directly fetches secrets/variables from database
- Used for single-tenant deployments with trusted code

Untrusted mode (TRACECAT__EXECUTOR_TRUST_MODE=untrusted):
- No access to DB credentials
- Uses pre-resolved secrets/variables passed in payload
- Used for multitenant deployments with untrusted code

Input/Output:
- Sandbox mode: Reads from /work/input.json, writes to /work/result.json
- Direct mode: Reads from stdin, writes to stdout
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

import orjson
from pydantic_core import to_jsonable_python

from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor.schemas import ExecutorActionErrorInfo, ResolvedContext
from tracecat.executor.service import run_action_from_input
from tracecat.executor.untrusted_runner import run_action_untrusted


class ExecutionErrorDict(TypedDict, total=False):
    """Error information from subprocess action execution."""

    # Always present
    type: str
    message: str

    # From ExecutorActionErrorInfo
    action_name: str
    filename: str
    function: str
    lineno: int | None
    loop_iteration: int | None
    loop_vars: dict[str, Any] | None

    # Fallback error fields
    traceback: str


class ExecutionResult(TypedDict):
    """Result from subprocess action execution."""

    success: bool
    result: Any | None
    error: NotRequired[ExecutionErrorDict | None]


def _run_trusted(input_data: dict[str, Any]) -> ExecutionResult:
    """Run action in trusted mode with direct DB access."""
    try:
        input_obj = RunActionInput.model_validate(input_data["input"])
        role = Role.model_validate(input_data["role"])

        result = asyncio.run(run_action_from_input(input=input_obj, role=role))

        return ExecutionResult(
            success=True,
            result=to_jsonable_python(result),
        )

    except Exception as e:
        action_name = (
            input_data.get("input", {}).get("task", {}).get("action", "unknown")
        )
        try:
            error_info = ExecutorActionErrorInfo.from_exc(e, action_name=action_name)
            error_dict = cast(ExecutionErrorDict, error_info.model_dump(mode="json"))
        except Exception:
            error_dict = ExecutionErrorDict(
                type=type(e).__name__,
                message=str(e),
                traceback=traceback.format_exc(),
            )

        return ExecutionResult(
            success=False,
            result=None,
            error=error_dict,
        )


def _run_untrusted(input_data: dict[str, Any]) -> ExecutionResult:
    """Run action in untrusted mode using pre-resolved secrets/variables.

    In this mode, we don't have DB credentials. Secrets and variables are
    pre-resolved by the caller and passed via 'resolved_context' in the payload.
    """
    try:
        input_obj = RunActionInput.model_validate(input_data["input"])
        role = Role.model_validate(input_data["role"])

        # Get resolved context from payload (pre-resolved by caller)
        resolved_context = ResolvedContext.model_validate(
            input_data.get("resolved_context", {})
        )

        result = asyncio.run(
            run_action_untrusted(
                input=input_obj, role=role, resolved_context=resolved_context
            )
        )

        return ExecutionResult(
            success=True,
            result=to_jsonable_python(result),
        )

    except Exception as e:
        action_name = (
            input_data.get("input", {}).get("task", {}).get("action", "unknown")
        )
        try:
            error_info = ExecutorActionErrorInfo.from_exc(e, action_name=action_name)
            error_dict = cast(ExecutionErrorDict, error_info.model_dump(mode="json"))
        except Exception:
            error_dict = ExecutionErrorDict(
                type=type(e).__name__,
                message=str(e),
                traceback=traceback.format_exc(),
            )

        return ExecutionResult(
            success=False,
            result=None,
            error=error_dict,
        )


def main() -> None:
    """Subprocess entrypoint."""
    # Determine trust mode from environment
    trust_mode = os.environ.get("TRACECAT__EXECUTOR_TRUST_MODE", "trusted")

    # Determine input source: file (sandbox) or stdin (direct)
    input_path = Path("/work/input.json")
    output_path = Path("/work/result.json")

    if input_path.exists():
        # Sandbox mode: read from file
        input_data = orjson.loads(input_path.read_bytes())
        use_file_io = True
    else:
        # Direct mode: read from stdin
        input_bytes = sys.stdin.buffer.read()
        input_data = orjson.loads(input_bytes)
        use_file_io = False

    # Execute action based on trust mode
    if trust_mode == "untrusted":
        result = _run_untrusted(input_data)
    else:
        result = _run_trusted(input_data)

    # Output result
    result_bytes = orjson.dumps(result, default=to_jsonable_python)

    if use_file_io:
        # Sandbox mode: write to file
        output_path.write_bytes(result_bytes)
    else:
        # Direct mode: write to stdout
        sys.stdout.buffer.write(result_bytes)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
