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
from tracecat.logger import logger
from tracecat.registry.actions.service import (
    IndexedActionResult,
    RegistryActionsService,
)


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
    indexed_result: IndexedActionResult,
    *,
    tool_approvals: dict[str, bool] | None = None,
) -> Tool:
    """Create a Tool from a registry action using index-based lookup.

    Args:
        action_name: Full canonical action name (e.g., "core.http_request")
        indexed_result: Pre-fetched IndexedActionResult containing index entry and manifest
        tool_approvals: Tool approval requirements by tool name

    Returns:
        A Tool with canonical action name and JSON schema

    Raises:
        ValueError: If action has no description or is not found in manifest
    """
    # Get requires_approval from index_entry options
    options = indexed_result.index_entry.options or {}
    requires_approval = options.get("requires_approval", False)

    if tool_approvals and action_name in tool_approvals:
        requires_approval = tool_approvals[action_name]

    # Read description and JSON schema directly from the manifest action.
    # This avoids importing the action module (which may not be installed
    # in this process for custom/git-synced UDF actions). The manifest
    # already stores the pre-computed interface from registry sync time.
    manifest_action = indexed_result.manifest.actions.get(action_name)
    if not manifest_action:
        raise ValueError(f"Action '{action_name}' not found in manifest")

    description = manifest_action.description
    if not description:
        raise ValueError(f"Action '{action_name}' has no description")

    parameters_json_schema = manifest_action.interface.get("expects", {})

    # Guard against malformed manifests where expects is a field map
    # (e.g. {"input": "str"}) rather than a proper JSON Schema object.
    # Valid schemas from model_json_schema() always have "properties" or "type".
    if (
        parameters_json_schema
        and "properties" not in parameters_json_schema
        and "type" not in parameters_json_schema
    ):
        raise ValueError(
            f"Action '{action_name}' has a malformed interface schema: "
            f"expected a JSON Schema object with 'type'/'properties', "
            f"got {set(parameters_json_schema.keys())}"
        )

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
    indexed_result: IndexedActionResult,
    action_name: str,
    tool_approvals: dict[str, bool] | None = None,
) -> CreateToolResult | None:
    """Create a single tool from an indexed action result.

    Args:
        indexed_result: The indexed action result containing index entry and manifest
        action_name: The canonical action name (namespace.name)
        tool_approvals: Tool approval requirements by tool name

    Returns:
        CreateToolResult containing the tool and metadata, or None if creation failed
    """
    collected_secrets: set[RegistrySecretType] = set()

    try:
        # Use manifest-based secret aggregation instead of DB queries
        action_secrets = RegistryActionsService.aggregate_secrets_from_manifest(
            indexed_result.manifest,
            action_name,
        )
        collected_secrets.update(action_secrets)

        tool = await create_tool_from_registry(
            action_name,
            indexed_result,
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
    """Build tools from a list of actions using index-based lookups.

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
        # Use index-based lookup instead of querying action tables
        indexed_results = await service.get_actions_from_index(actions)

        failed_actions: set[str] = set()

        # Check for missing actions
        found_actions = set(indexed_results.keys())
        missing_actions = set(actions) - found_actions

        for action_name, indexed_result in indexed_results.items():
            logger.debug(f"Building tool for action: {action_name}")

            # Apply namespace filtering if specified
            if namespaces:
                if not any(action_name.startswith(ns) for ns in namespaces):
                    continue

            result = await create_single_tool(
                indexed_result,
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


def denormalize_tool_name(tool_name: str) -> str:
    """Convert MCP tool name format to canonical action name.

    MCP tool names use double underscores, canonical names use dots.

    Args:
        tool_name: MCP format name (e.g., "core__cases__list_cases")

    Returns:
        Canonical action name (e.g., "core.cases.list_cases")
    """
    return tool_name.replace("__", ".")
