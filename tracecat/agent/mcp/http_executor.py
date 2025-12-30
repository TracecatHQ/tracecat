"""Sandboxed action executor for the trusted HTTP MCP server.

All actions are executed in subprocesses for isolation, following the
pattern established in tracecat/agent/_subprocess_runner.py.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import orjson
from pydantic_core import to_jsonable_python

from tracecat.agent.mcp.tokens import MCPTokenClaims
from tracecat.contexts import ctx_role
from tracecat.logger import logger


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
    """Execute an action in a subprocess with sandboxing.

    All actions are executed via subprocess using the _subprocess_runner module,
    which handles secrets, context, and both UDF and template actions.

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
    # Validate action is allowed (check against dict keys)
    if action_name not in claims.allowed_actions:
        logger.warning(
            "Action not allowed",
            action_name=action_name,
            allowed_actions=list(claims.allowed_actions.keys()),
        )
        raise ActionNotAllowedError(
            f"Action '{action_name}' is not in allowed actions for this token"
        ) from None

    # Set role context for the execution
    ctx_role.set(claims.role)
    role_data = claims.role.model_dump(mode="json")

    logger.info(
        "Executing action via subprocess",
        action_name=action_name,
        workspace_id=str(claims.role.workspace_id),
        run_id=claims.run_id,
    )

    # Use temp files for input/output to avoid pipe buffer limits
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "input.json"
        output_path = Path(tmpdir) / "output.json"

        # Write input payload to file
        payload = orjson.dumps(
            {
                "action_name": action_name,
                "args": args,
                "role": role_data,
            },
            default=to_jsonable_python,
        )
        input_path.write_bytes(payload)

        # Run the subprocess
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tracecat.agent._subprocess_runner",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )

        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError as err:
            proc.kill()
            await proc.wait()
            logger.error(
                "Action execution timed out",
                action_name=action_name,
                timeout_seconds=timeout_seconds,
            )
            raise ActionExecutionError(
                f"Action execution timed out after {timeout_seconds}s"
            ) from err
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            raise

        if proc.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown subprocess error"
            logger.error(
                "Subprocess failed",
                action_name=action_name,
                returncode=proc.returncode,
                stderr=error_msg,
            )
            raise ActionExecutionError(
                f"Action execution failed: {error_msg}"
            ) from None

        # Read output from file
        if not output_path.exists():
            logger.error(
                "Subprocess did not produce output file",
                action_name=action_name,
            )
            raise ActionExecutionError(
                "Action execution failed: no output produced"
            ) from None

        try:
            result = orjson.loads(output_path.read_bytes())
        except orjson.JSONDecodeError as err:
            logger.error(
                "Failed to parse subprocess output",
                action_name=action_name,
                error=str(err),
            )
            raise ActionExecutionError(f"Failed to parse action result: {err}") from err

    if not result.get("success"):
        error = result.get("error", "Unknown error")
        logger.error("Action execution failed", action_name=action_name, error=error)
        raise ActionExecutionError(error) from None

    logger.info(
        "Action executed successfully",
        action_name=action_name,
        workspace_id=str(claims.role.workspace_id),
    )

    return result.get("result")
