"""Subprocess entrypoint for sandboxed action execution.

This module is invoked by nsjail or direct subprocess execution to run
registry actions in isolation. It supports two modes:

Trusted mode (default):
- Has access to DB credentials via environment variables
- Directly fetches secrets/variables from database
- Used for single-tenant deployments with trusted code

Untrusted mode (TRACECAT__EXECUTOR_TRUST_MODE=untrusted):
- No access to DB credentials
- Uses SDK to call back to Tracecat API for secrets/variables
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
from typing import Any

import orjson
from pydantic_core import to_jsonable_python


def _run_trusted(input_data: dict[str, Any]) -> dict[str, Any]:
    """Run action in trusted mode with direct DB access."""
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput
    from tracecat.executor.schemas import ExecutorActionErrorInfo
    from tracecat.executor.service import run_action_from_input

    try:
        input_obj = RunActionInput.model_validate(input_data["input"])
        role = Role.model_validate(input_data["role"])

        result = asyncio.run(run_action_from_input(input=input_obj, role=role))

        return {
            "success": True,
            "result": to_jsonable_python(result),
            "error": None,
        }

    except Exception as e:
        action_name = (
            input_data.get("input", {}).get("task", {}).get("action", "unknown")
        )
        try:
            error_info = ExecutorActionErrorInfo.from_exc(e, action_name=action_name)
            error_dict = error_info.model_dump(mode="json")
        except Exception:
            error_dict = {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc(),
            }

        return {
            "success": False,
            "result": None,
            "error": error_dict,
        }


def _run_untrusted(input_data: dict[str, Any]) -> dict[str, Any]:
    """Run action in untrusted mode using SDK for secrets/variables.

    In this mode, we don't have DB credentials. Instead we use the
    Tracecat SDK to call back to the API for secrets and variables.
    """
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput
    from tracecat.executor.schemas import ExecutorActionErrorInfo
    from tracecat.executor.untrusted_runner import run_action_untrusted

    try:
        input_obj = RunActionInput.model_validate(input_data["input"])
        role = Role.model_validate(input_data["role"])

        result = asyncio.run(run_action_untrusted(input=input_obj, role=role))

        return {
            "success": True,
            "result": to_jsonable_python(result),
            "error": None,
        }

    except Exception as e:
        action_name = (
            input_data.get("input", {}).get("task", {}).get("action", "unknown")
        )
        try:
            error_info = ExecutorActionErrorInfo.from_exc(e, action_name=action_name)
            error_dict = error_info.model_dump(mode="json")
        except Exception:
            error_dict = {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc(),
            }

        return {
            "success": False,
            "result": None,
            "error": error_dict,
        }


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
