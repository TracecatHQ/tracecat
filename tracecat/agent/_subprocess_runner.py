"""Subprocess runner for executing registry actions in isolation.

This module provides a way to run registry actions in a separate subprocess,
avoiding load on the executor service which handles workflow executions.

Uses temporary files for input/output to avoid Unix pipe buffer limits (64KB).
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

import orjson
from pydantic_core import to_jsonable_python

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.dsl.common import create_default_execution_context
from tracecat.executor.service import (
    _run_action_direct,
    get_workspace_variables,
    run_template_action,
)
from tracecat.expressions.eval import collect_expressions, eval_templated_object
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.loaders import get_bound_action_from_manifest
from tracecat.secrets import secrets_manager


class ActionNotFoundError(Exception):
    """Raised when an action is not found in the registry."""


async def run_action(
    action_name: str,
    args: dict[str, Any],
    role_data: dict[str, Any] | None = None,
) -> Any:
    """Run a registry action with the given arguments.

    Uses the same implementation as the original call_tracecat_action from main,
    which properly handles secrets, context, template actions, etc.

    Args:
        action_name: The action to execute
        args: Arguments to pass to the action
        role_data: Serialized Role data for authorization context
    """
    # Set the role context if provided
    if role_data:
        role = Role.model_validate(role_data)
        ctx_role.set(role)
    else:
        role = None

    # Load action from index + manifest (not RegistryAction table)
    async with RegistryActionsService.with_session(role=role) as service:
        indexed_result = await service.get_action_from_index(action_name)
        if indexed_result is None:
            raise ActionNotFoundError(f"Action '{action_name}' not found in registry")

    # Get manifest action and aggregate secrets from manifest
    manifest_action = indexed_result.manifest.actions.get(action_name)
    if manifest_action is None:
        raise ActionNotFoundError(f"Action '{action_name}' not found in manifest")

    action_secrets = set(
        RegistryActionsService.aggregate_secrets_from_manifest(
            indexed_result.manifest, action_name
        )
    )
    bound_action = get_bound_action_from_manifest(
        manifest_action, indexed_result.origin, mode="execution"
    )

    collected = collect_expressions(args)
    secrets = await secrets_manager.get_action_secrets(
        secret_exprs=collected.secrets, action_secrets=action_secrets
    )
    ws_vars = await get_workspace_variables(variable_exprs=collected.variables)

    # Call action with secrets in environment
    context = create_default_execution_context()
    context["SECRETS"] = secrets
    context["VARS"] = ws_vars

    flattened_secrets = secrets_manager.flatten_secrets(secrets)

    evaled_args = eval_templated_object(args, operand=context)
    with secrets_manager.env_sandbox(flattened_secrets):
        # Call directly based on action type
        if bound_action.is_template:
            # For templates, pass the context with secrets
            result = await run_template_action(
                action=bound_action,
                args=evaled_args,
                context=context,
            )
        else:
            # UDFs can be called directly - secrets are now in the environment
            result = await _run_action_direct(
                action=bound_action,
                args=evaled_args,
            )

    return result


def main() -> None:
    """Entry point for subprocess execution.

    Uses temporary files for input/output to avoid pipe buffer limits.

    Args (via argparse):
        --input: Path to input JSON file containing {"action_name": "...", "args": {...}}
        --output: Path to output JSON file for {"success": true, "result": ...}
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to input JSON file")
    parser.add_argument("--output", required=True, help="Path to output JSON file")
    cli_args = parser.parse_args()

    input_path = Path(cli_args.input)
    output_path = Path(cli_args.output)

    # Read input from file
    try:
        payload = orjson.loads(input_path.read_bytes())
        action_name = payload["action_name"]
        action_args = payload["args"]
        role_data = payload.get("role")
    except (orjson.JSONDecodeError, KeyError, FileNotFoundError) as e:
        output = {"success": False, "error": f"Invalid input: {e}"}
        output_path.write_bytes(orjson.dumps(output, default=to_jsonable_python))
        return
    else:
        # Best-effort cleanup once input has been read.
        try:
            input_path.unlink(missing_ok=True)
        except OSError:
            pass

    # Run the action
    try:
        result = asyncio.run(run_action(action_name, action_args, role_data))
        output = {"success": True, "result": result}
    except Exception as e:
        output = {"success": False, "error": str(e)}

    # Write output to file
    output_path.write_bytes(orjson.dumps(output, default=to_jsonable_python))


if __name__ == "__main__":
    main()
