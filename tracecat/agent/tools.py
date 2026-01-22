"""Functions that create and call tools added to the agent.

This module provides harness-agnostic tool building from the Tracecat registry.
Tools use canonical action names (with dots, e.g., 'core.cases.list_cases').
Harness-specific adapters are responsible for converting to their required format.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson
from tracecat_registry import RegistrySecretType

from tracecat.agent.types import Tool
from tracecat.config import TRACECAT__AGENT_MAX_TOOLS
from tracecat.contexts import ctx_role
from tracecat.db.models import RegistryAction
from tracecat.expressions.expectations import create_expectation_model
from tracecat.logger import logger
from tracecat.registry.actions.bound import BoundRegistryAction
from tracecat.registry.actions.schemas import RegistryActionOptions
from tracecat.registry.actions.service import RegistryActionsService


class ToolExecutionError(Exception):
    """Raised when tool execution fails."""


async def call_tracecat_action(
    action_name: str,
    args: dict[str, Any],
) -> Any:
    """Execute a Tracecat action in a subprocess.

    This spawns a separate process to run the action, providing process isolation
    and avoiding load on the executor service which handles workflow executions.

    Uses temporary files for input/output to avoid Unix pipe buffer limits (64KB).

    Args:
        action_name: The action to execute (e.g., "core.http_request")
        args: Arguments to pass to the action

    Returns:
        The action result

    Raises:
        ToolExecutionError: If action execution fails
    """
    role = ctx_role.get()
    role_data = role.model_dump(mode="json") if role else None

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "input.json"
        output_path = Path(tmpdir) / "output.json"

        payload = orjson.dumps(
            {
                "action_name": action_name,
                "args": args,
                "role": role_data,
            }
        )
        input_path.write_bytes(payload)

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
            _, stderr = await proc.communicate()
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
            raise ToolExecutionError(f"Action execution failed: {error_msg}")

        if not output_path.exists():
            logger.error(
                "Subprocess did not produce output file",
                action_name=action_name,
            )
            raise ToolExecutionError("Action execution failed: no output produced")

        try:
            result = orjson.loads(output_path.read_bytes())
        except orjson.JSONDecodeError as e:
            logger.error(
                "Failed to parse subprocess output",
                action_name=action_name,
                error=str(e),
            )
            raise ToolExecutionError(f"Failed to parse action result: {e}") from e
        else:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass

    if not result.get("success"):
        error = result.get("error", "Unknown error")
        logger.error("Action execution failed", action_name=action_name, error=error)
        raise ToolExecutionError(error)

    return result.get("result")


async def create_tool_from_registry(
    action_name: str,
    ra: RegistryAction | None = None,
    *,
    service: RegistryActionsService | None = None,
    tool_approvals: dict[str, bool] | None = None,
) -> Tool:
    """Create a Tool from a registry action.

    Args:
        action_name: Full canonical action name (e.g., "core.http_request")
        ra: Optional pre-fetched RegistryAction
        service: Optional RegistryActionsService instance
        tool_approvals: Tool approval requirements by tool name

    Returns:
        A Tool with canonical action name and JSON schema

    Raises:
        ValueError: If action has no description
    """
    if service is None:
        async with RegistryActionsService.with_session() as _service:
            return await create_tool_from_registry(
                action_name,
                ra,
                service=_service,
                tool_approvals=tool_approvals,
            )

    reg_action = ra or await service.get_action(action_name)
    options = RegistryActionOptions.model_validate(reg_action.options)
    bound_action = service.get_bound(reg_action, mode="execution")

    # Extract metadata from the bound action
    description, model_cls = _extract_action_metadata(bound_action)

    if not description:
        raise ValueError(f"Action '{action_name}' has no description")

    # Get JSON schema from model class
    parameters_json_schema = (
        model_cls.model_json_schema() if hasattr(model_cls, "model_json_schema") else {}  # type: ignore[call-non-callable]
    )

    # Determine requires_approval
    override = (tool_approvals or {}).get(action_name)
    if override is not None:
        requires_approval = override
    else:
        requires_approval = options.requires_approval

    return Tool(
        name=action_name,  # Canonical name with dots
        description=description,
        parameters_json_schema=parameters_json_schema,
        requires_approval=requires_approval,
    )


@dataclass
class CreateToolResult:
    """Result of creating a single tool from a registry action."""

    tool: Tool
    """The created tool."""
    collected_secrets: set[RegistrySecretType]
    """Secrets collected during tool creation."""
    action_name: str
    """The canonical action name (with dots)."""


async def create_single_tool(
    service: RegistryActionsService,
    ra: RegistryAction,
    action_name: str,
    tool_approvals: dict[str, bool] | None = None,
) -> CreateToolResult | None:
    """Create a single tool from a registry action.

    Args:
        service: The registry actions service instance
        ra: The registry action to create a tool from
        action_name: The canonical action name (namespace.name)
        tool_approvals: Tool approval requirements by tool name

    Returns:
        CreateToolResult containing the tool and metadata, or None if creation failed
    """
    collected_secrets: set[RegistrySecretType] = set()

    try:
        action_secrets = await service.fetch_all_action_secrets(ra)
        collected_secrets.update(action_secrets)

        tool = await create_tool_from_registry(
            action_name,
            ra,
            service=service,
            tool_approvals=tool_approvals,
        )

        return CreateToolResult(
            tool=tool,
            collected_secrets=collected_secrets,
            action_name=action_name,
        )
    except Exception as e:
        logger.error(
            "Failed to create tool from registry action",
            action_name=action_name,
            error=str(e),
        )
        return None


@dataclass
class BuildToolsResult:
    """Result of building tools from registry actions."""

    tools: list[Tool]
    """List of tools with canonical action names."""
    collected_secrets: set[RegistrySecretType]
    """All secrets required by the tools."""


async def build_agent_tools(
    namespaces: list[str] | None = None,
    actions: list[str] | None = None,
    tool_approvals: dict[str, bool] | None = None,
    max_tools: int = TRACECAT__AGENT_MAX_TOOLS,
) -> BuildToolsResult:
    """Build tools from a list of actions.

    Args:
        namespaces: Optional list of namespace prefixes to filter by
        actions: List of canonical action names to build tools for
        tool_approvals: Tool approval requirements by tool name
        max_tools: Maximum number of tools allowed

    Returns:
        BuildToolsResult containing tools and collected secrets

    Raises:
        ValueError: If actions are missing/failed or max_tools exceeded
    """
    if not actions:
        return BuildToolsResult(tools=[], collected_secrets=set())

    tools: list[Tool] = []
    collected_secrets: set[RegistrySecretType] = set()

    async with RegistryActionsService.with_session() as service:
        selected_actions = await service.get_actions(actions)

        failed_actions: set[str] = set()
        missing_actions: set[str] = set()

        if actions:
            found_actions = {f"{ra.namespace}.{ra.name}" for ra in selected_actions}
            missing_actions = {
                action_name
                for action_name in actions
                if action_name not in found_actions
            }

        for ra in selected_actions:
            action_name = f"{ra.namespace}.{ra.name}"
            logger.debug(f"Building tool for action: {action_name}")

            # Apply namespace filtering if specified
            if namespaces:
                if not any(action_name.startswith(ns) for ns in namespaces):
                    continue

            result = await create_single_tool(
                service,
                ra,
                action_name,
                tool_approvals=tool_approvals,
            )

            if result is None:
                failed_actions.add(action_name)
                continue

            collected_secrets.update(result.collected_secrets)
            tools.append(result.tool)

    if missing_actions or failed_actions:
        details: list[str] = []
        if missing_actions:
            missing_list = "\n".join(
                f"- {action}" for action in sorted(missing_actions)
            )
            details.append("Requested actions not found in registry:\n" + missing_list)
        if failed_actions:
            failed_list = "\n".join(f"- {action}" for action in sorted(failed_actions))
            details.append("Failed to build the following actions:\n" + failed_list)

        raise ValueError(
            "Unable to build the requested tools:\n" + "\n\n".join(details)
        )

    if max_tools > 0 and len(tools) > max_tools:
        raise ValueError(f"Cannot request more than {max_tools} tools")

    return BuildToolsResult(tools=tools, collected_secrets=collected_secrets)


def _extract_action_metadata(bound_action: BoundRegistryAction) -> tuple[str, type]:
    """Extract description and model class from a bound action.

    Args:
        bound_action: The bound action from the registry

    Returns:
        Tuple of (description, model_cls)

    Raises:
        ValueError: If template action is not set
    """
    if bound_action.type == "template":
        if not bound_action.template_action:
            raise ValueError("Template action is not set")

        description = (
            bound_action.template_action.definition.description
            or bound_action.description
        )

        expects = bound_action.template_action.definition.expects
        model_cls = create_expectation_model(
            expects, bound_action.template_action.definition.action.replace(".", "__")
        )
    else:
        description = bound_action.description
        model_cls = bound_action.args_cls

    return description, model_cls


def denormalize_tool_name(tool_name: str) -> str:
    """Convert MCP tool name format to canonical action name.

    MCP tool names use double underscores, canonical names use dots.

    Args:
        tool_name: MCP format name (e.g., "core__cases__list_cases")

    Returns:
        Canonical action name (e.g., "core.cases.list_cases")
    """
    return tool_name.replace("__", ".")
