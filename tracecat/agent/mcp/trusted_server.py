"""Trusted MCP server for Tracecat agent.

This FastMCP server exposes a token-scoped catalog of concrete registry,
internal, and user MCP tools. It runs outside the sandbox with full network
access.

Run with uvicorn on a Unix socket:
    uvicorn tracecat.agent.mcp.trusted_server:app --uds /var/run/tracecat/mcp.sock

All action execution uses nsjail sandboxing. To test locally, run in a
Docker container with nsjail installed (e.g., the executor image).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec
from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from tracecat.agent.common.types import MCPHttpServerConfig, MCPToolDefinition
from tracecat.agent.mcp.executor import (
    ActionExecutionError,
    ActionNotAllowedError,
    execute_action,
)
from tracecat.agent.mcp.internal_tools import (
    INTERNAL_TOOL_HANDLERS,
    InternalToolError,
    get_builder_internal_tool_definitions,
)
from tracecat.agent.mcp.metadata import (
    build_registry_tool_schema,
    extract_proxy_tool_call_id,
)
from tracecat.agent.mcp.user_client import UserMCPClient
from tracecat.agent.mcp.utils import (
    action_name_to_mcp_tool_name,
    fetch_tool_definitions,
    mcp_tool_name_to_action_name,
    normalize_mcp_tool_name,
)
from tracecat.agent.tokens import MCPTokenClaims, UserMCPServerClaim, verify_mcp_token
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role
from tracecat.exceptions import (
    BuiltinRegistryHasNoSelectionError,
    EntitlementRequired,
    ExecutionError,
)
from tracecat.logger import logger
from tracecat.registry.lock.service import RegistryLockService


class TracecatScopedTool(Tool):
    """A concrete token-scoped tool exposed by the trusted MCP server."""

    claims: SkipJsonSchema[MCPTokenClaims] = Field(exclude=True)

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the concrete tool using the claims captured at lookup time."""
        result = await call_token_scoped_tool(self.name, arguments, self.claims)
        return ToolResult(content=result)


class TokenScopedFastMCP(FastMCP[None]):
    """FastMCP server whose visible tools are derived from the caller token."""

    async def list_tools(self, *, run_middleware: bool = True) -> Sequence[Tool]:
        del run_middleware
        return await build_token_scoped_tools(_claims_from_request())

    async def get_tool(
        self,
        name: str,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        del version
        claims = _claims_from_request()
        tools = await build_token_scoped_tools(claims)
        return next((tool for tool in tools if tool.name == name), None)


mcp = TokenScopedFastMCP("tracecat-actions")


def _set_role_context(claims: MCPTokenClaims) -> Role:
    """Construct Role from claims and set the context variable.

    This must be called before any service that requires organization context.
    """
    role = Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=claims.workspace_id,
        organization_id=claims.organization_id,
        user_id=claims.user_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-mcp"],
    )
    ctx_role.set(role)
    return role


def _safe_error_type(exc: Exception) -> str:
    """Return only the exception class name for log-safe reporting."""
    return type(exc).__name__


def _claims_from_token(token: str) -> MCPTokenClaims:
    """Verify an MCP token and return claims."""
    try:
        return verify_mcp_token(token)
    except ValueError as e:
        logger.warning("MCP token verification failed", error_type=_safe_error_type(e))
        raise ToolError("Authentication failed") from None


def _claims_from_authorization_header(authorization: str | None) -> MCPTokenClaims:
    """Verify a bearer authorization header and return MCP claims."""
    if not authorization:
        raise ToolError("Authentication failed")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ToolError("Authentication failed")

    return _claims_from_token(token)


def _claims_from_request() -> MCPTokenClaims:
    """Extract and verify MCP token claims from the active HTTP request."""
    headers = get_http_headers(include={"authorization"})
    return _claims_from_authorization_header(headers.get("authorization"))


def _registry_action_names(claims: MCPTokenClaims) -> list[str]:
    return [
        name
        for name in claims.allowed_actions
        if not name.startswith("internal.")
        and UserMCPClient.parse_user_mcp_tool_name(name) is None
    ]


def _internal_tool_names(claims: MCPTokenClaims) -> list[str]:
    names: list[str] = []
    for name in [*claims.allowed_internal_tools, *claims.allowed_actions]:
        if name.startswith("internal.") and name not in names:
            names.append(name)
    return names


def _user_mcp_tool_names(claims: MCPTokenClaims) -> set[str]:
    return {
        name
        for name in claims.allowed_actions
        if UserMCPClient.parse_user_mcp_tool_name(name) is not None
    }


def _user_mcp_config(server: UserMCPServerClaim) -> MCPHttpServerConfig:
    config: MCPHttpServerConfig = {
        "type": "http",
        "name": server.name,
        "url": server.url,
        "transport": server.transport,
        "headers": server.headers,
    }
    if server.timeout is not None:
        config["timeout"] = server.timeout
    return config


def _build_scoped_tool(
    *,
    tool_name: str,
    description: str,
    parameters_json_schema: dict[str, Any],
    registry_tool: bool = False,
    claims: MCPTokenClaims,
) -> TracecatScopedTool:
    schema = (
        build_registry_tool_schema(parameters_json_schema)
        if registry_tool
        else parameters_json_schema
    )
    return TracecatScopedTool(
        name=tool_name,
        description=description,
        parameters=schema,
        claims=claims,
    )


async def _discover_allowed_user_mcp_tools(
    claims: MCPTokenClaims,
) -> dict[str, MCPToolDefinition]:
    allowed_user_tools = _user_mcp_tool_names(claims)
    if not allowed_user_tools or not claims.user_mcp_servers:
        return {}

    client = UserMCPClient(
        [_user_mcp_config(server) for server in claims.user_mcp_servers]
    )
    discovered = await client.discover_tools()
    return {
        name: definition
        for name, definition in discovered.items()
        if name in allowed_user_tools
    }


async def build_token_scoped_tools(claims: MCPTokenClaims) -> list[Tool]:
    """Build the MCP tool catalog visible to one verified token."""
    _set_role_context(claims)
    tools: list[Tool] = []

    registry_action_names = _registry_action_names(claims)
    registry_definitions = await fetch_tool_definitions(registry_action_names)
    for action_name in registry_action_names:
        if definition := registry_definitions.get(action_name):
            tools.append(
                _build_scoped_tool(
                    tool_name=action_name_to_mcp_tool_name(action_name),
                    description=definition.description,
                    parameters_json_schema=definition.parameters_json_schema,
                    registry_tool=True,
                    claims=claims,
                )
            )

    internal_definitions = get_builder_internal_tool_definitions()
    for tool_name in _internal_tool_names(claims):
        if definition := internal_definitions.get(tool_name):
            tools.append(
                _build_scoped_tool(
                    tool_name=action_name_to_mcp_tool_name(tool_name),
                    description=definition.description,
                    parameters_json_schema=definition.parameters_json_schema,
                    claims=claims,
                )
            )

    try:
        user_mcp_definitions = await _discover_allowed_user_mcp_tools(claims)
    except Exception as e:
        logger.warning(
            "Failed to discover token-scoped user MCP tools",
            error_type=_safe_error_type(e),
            workspace_id=str(claims.workspace_id),
        )
        user_mcp_definitions = {}
    for tool_name in claims.allowed_actions:
        if definition := user_mcp_definitions.get(tool_name):
            tools.append(
                _build_scoped_tool(
                    tool_name=tool_name,
                    description=definition.description,
                    parameters_json_schema=definition.parameters_json_schema,
                    claims=claims,
                )
            )

    return tools


async def _execute_registry_action(
    action_name: str,
    args: dict[str, Any],
    claims: MCPTokenClaims,
    *,
    tool_call_id: str | None = None,
) -> str:
    """Execute one authorized registry action and return JSON text."""
    normalized_action_name = normalize_mcp_tool_name(action_name)
    if normalized_action_name not in claims.allowed_actions:
        logger.warning(
            "Registry action not authorized",
            action_name=normalized_action_name,
            workspace_id=str(claims.workspace_id),
        )
        raise ToolError(f"Tool '{normalized_action_name}' not authorized")

    _set_role_context(claims)

    try:
        async with RegistryLockService.with_session() as lock_service:
            registry_lock = await lock_service.resolve_lock_with_bindings(
                {normalized_action_name}
            )

        result = await execute_action(
            normalized_action_name,
            args,
            claims,
            registry_lock,
            tool_call_id=tool_call_id,
        )
        return json.dumps(result, default=str)
    except (ActionExecutionError, ActionNotAllowedError) as e:
        raise ToolError(str(e)) from e
    except ExecutionError as e:
        error_msg = e.info.message if e.info else str(e)
        logger.warning(
            "Action execution error",
            action_name=normalized_action_name,
            workspace_id=str(claims.workspace_id),
            error_type=e.info.type if e.info else "unknown",
            error=error_msg,
        )
        raise ToolError(error_msg) from e
    except EntitlementRequired as e:
        raise ToolError(str(e)) from e
    except BuiltinRegistryHasNoSelectionError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        logger.error(
            "Action execution failed",
            action_name=normalized_action_name,
            workspace_id=str(claims.workspace_id),
            error_type=_safe_error_type(e),
        )
        raise ToolError("Action execution failed") from None


async def _execute_user_mcp(
    server_name: str,
    tool_name: str,
    args: dict[str, Any],
    claims: MCPTokenClaims,
) -> str:
    """Execute one authorized user MCP tool and return JSON text."""
    _set_role_context(claims)
    scoped_tool_name = f"mcp__{server_name}__{tool_name}"
    if scoped_tool_name not in claims.allowed_actions:
        logger.warning(
            "User MCP tool not authorized",
            server_name=server_name,
            tool_name=tool_name,
            workspace_id=str(claims.workspace_id),
        )
        raise ToolError(f"Tool '{scoped_tool_name}' not authorized")

    server_config = None
    for cfg in claims.user_mcp_servers:
        if cfg.name == server_name:
            server_config = cfg
            break

    if server_config is None:
        logger.warning(
            "User MCP server not found in claims",
            server_name=server_name,
            workspace_id=str(claims.workspace_id),
        )
        raise ToolError(f"User MCP server '{server_name}' not authorized") from None

    try:
        client = UserMCPClient([_user_mcp_config(server_config)])
        result = await client.call_tool(server_name, tool_name, args)

        logger.info(
            "User MCP tool executed successfully",
            server_name=server_name,
            tool_name=tool_name,
            workspace_id=str(claims.workspace_id),
        )

        return json.dumps(result, default=str)
    except ToolError:
        raise
    except Exception as e:
        logger.error(
            "User MCP tool execution failed",
            server_name=server_name,
            tool_name=tool_name,
            workspace_id=str(claims.workspace_id),
            error_type=_safe_error_type(e),
        )
        raise ToolError(
            f"User MCP tool '{tool_name}' on server '{server_name}' failed"
        ) from None


async def _execute_internal(
    tool_name: str,
    args: dict[str, Any],
    claims: MCPTokenClaims,
) -> str:
    """Execute one authorized internal tool and return JSON text."""
    _set_role_context(claims)
    if tool_name not in claims.allowed_internal_tools:
        logger.warning(
            "Internal tool not authorized",
            tool_name=tool_name,
            allowed_internal_tools=claims.allowed_internal_tools,
        )
        raise ToolError(f"Tool '{tool_name}' not authorized")

    handler = INTERNAL_TOOL_HANDLERS.get(tool_name)
    if not handler:
        logger.warning("Unknown internal tool", tool_name=tool_name)
        raise ToolError(f"Unknown internal tool: {tool_name}")

    try:
        result = await handler(args, claims)
        return json.dumps(result, default=str)

    except InternalToolError as e:
        logger.warning(
            "Internal tool execution error",
            tool_name=tool_name,
            workspace_id=str(claims.workspace_id),
            error_type=_safe_error_type(e),
        )
        raise ToolError(str(e)) from e

    except Exception as e:
        logger.error(
            "Internal tool execution failed",
            tool_name=tool_name,
            workspace_id=str(claims.workspace_id),
            error_type=_safe_error_type(e),
        )
        raise ToolError("Internal tool execution failed") from None


async def call_token_scoped_tool(
    tool_name: str,
    args: dict[str, Any],
    claims: MCPTokenClaims,
) -> str:
    """Route one token-scoped concrete MCP tool call."""
    forwarded_args = dict(args)
    if parsed := UserMCPClient.parse_user_mcp_tool_name(tool_name):
        server_name, original_tool_name = parsed
        return await _execute_user_mcp(
            server_name,
            original_tool_name,
            forwarded_args,
            claims,
        )

    action_name = mcp_tool_name_to_action_name(tool_name)
    if action_name.startswith("internal."):
        return await _execute_internal(action_name, forwarded_args, claims)

    tool_call_id = extract_proxy_tool_call_id(forwarded_args)
    return await _execute_registry_action(
        action_name,
        forwarded_args,
        claims,
        tool_call_id=tool_call_id,
    )


app = mcp.http_app(path="/mcp")
