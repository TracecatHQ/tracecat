"""Trusted MCP server for Tracecat agent.

A FastMCP-based server with tools for executing both:
1. Registry actions (via execute_action_tool)
2. User MCP server tools (via execute_user_mcp_tool)

The proxy MCP server (inside nsjail) handles tool schema/explicitness for Claude.
This trusted server runs outside the sandbox with full network access.

Run with uvicorn on a Unix socket:
    uvicorn tracecat.agent.mcp.trusted_server:app --uds /var/run/tracecat/mcp.sock

All action execution uses nsjail sandboxing. To test locally, run in a
Docker container with nsjail installed (e.g., the executor image).
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from tracecat.agent.common.types import MCPHttpServerConfig, is_http_mcp_server
from tracecat.agent.mcp.executor import ActionExecutionError, execute_action
from tracecat.agent.mcp.internal_tools import (
    INTERNAL_TOOL_HANDLERS,
    InternalToolError,
)
from tracecat.agent.mcp.user_client import UserMCPClient
from tracecat.agent.mcp.utils import normalize_mcp_tool_name
from tracecat.agent.tokens import MCPTokenClaims, verify_mcp_token
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

mcp = FastMCP("tracecat-actions")


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


@mcp.tool
async def execute_action_tool(
    action_name: str,
    args: dict[str, Any],
    auth_token: str,
    tool_call_id: str | None = None,
) -> str:
    """Execute any Tracecat registry action.

    Args:
        action_name: The action to execute (e.g., "core.http_request")
        args: Arguments to pass to the action
        auth_token: JWT token for authentication and authorization

    Returns:
        JSON-encoded result from the action
    """
    try:
        claims = verify_mcp_token(auth_token)
    except ValueError as e:
        logger.warning("MCP token verification failed", error_type=_safe_error_type(e))
        raise ToolError("Authentication failed") from None

    normalized_action_name = normalize_mcp_tool_name(action_name)

    # Set role context before any service calls that require organization context
    _set_role_context(claims)

    try:
        # Resolve registry lock for this action
        # This is called from the proxy MCP server inside nsjail, so we need
        # to resolve the lock here (unlike execute_approved_tools_activity which
        # receives the lock from the workflow)
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
    except ActionExecutionError as e:
        raise ToolError(str(e)) from e
    except ExecutionError as e:
        # ExecutionError contains user-facing error info (validation errors, etc.)
        # Propagate the actual error message so users can understand what went wrong
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
        # Unexpected platform errors - log full details but return generic message
        logger.error(
            "Action execution failed",
            action_name=normalized_action_name,
            workspace_id=str(claims.workspace_id),
            error_type=_safe_error_type(e),
        )
        raise ToolError("Action execution failed") from None


@mcp.tool
async def execute_user_mcp_tool(
    server_name: str,
    tool_name: str,
    args: dict[str, Any],
    auth_token: str,
) -> str:
    """Execute a tool on a user-defined MCP server.

    User MCP servers are authorized via the JWT claims (only ``name`` + source
    integration ``id`` cross the boundary). Secrets (OAuth bearers, custom
    auth headers, stdio env) are re-resolved here per call so they never
    enter Temporal history or JWT payloads.
    """
    try:
        claims = verify_mcp_token(auth_token)
    except ValueError as e:
        logger.warning("MCP token verification failed", error_type=_safe_error_type(e))
        raise ToolError("Authentication failed") from None

    role = _set_role_context(claims)

    ref = next(
        (s for s in claims.user_mcp_servers if s.name == server_name),
        None,
    )
    if ref is None:
        logger.warning(
            "User MCP server not found in claims",
            server_name=server_name,
            workspace_id=str(claims.workspace_id),
        )
        raise ToolError(f"User MCP server '{server_name}' not authorized") from None

    # Resolve metadata + secrets per call. Both come from the DB; no
    # secrets are carried through the claim itself.
    from tracecat.agent.preset.service import AgentPresetService

    if ref.id is None:
        # Legacy replay path: in-flight tokens minted before the refs-only
        # cutover. Use the inline url/headers from the claim. New tokens
        # always carry ``id`` and never hit this branch.
        if not ref.url:
            raise ToolError(f"User MCP server '{server_name}' missing url")
        config_dict: MCPHttpServerConfig = {
            "type": "http",
            "name": ref.name,
            "url": ref.url,
            "transport": ref.transport,
            "headers": ref.headers,
        }
        if ref.timeout is not None:
            config_dict["timeout"] = ref.timeout
    else:
        async with AgentPresetService.with_session(role=role) as svc:
            refs = await svc.resolve_mcp_integration_refs([str(ref.id)])
            if not refs:
                raise ToolError(f"MCP integration {ref.id} not found")
            metadata = refs[0]
            if not is_http_mcp_server(metadata):
                raise ToolError(
                    f"User MCP server '{server_name}' is not an HTTP server"
                )
            secrets = await svc.resolve_mcp_integration_secrets(ref.id)
        config_dict = {
            "type": "http",
            # Keep the call-time routing key stable. The DB row's display name
            # may have changed after tools were discovered or while a run was
            # paused, but the runtime still calls the claimed server name.
            "name": ref.name,
            "url": metadata["url"],
            "transport": metadata.get("transport", "http"),
            "headers": secrets or {},
        }
        timeout = metadata.get("timeout")
        if timeout is not None:
            config_dict["timeout"] = timeout

    try:
        client = UserMCPClient([config_dict])
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


@mcp.tool
async def execute_internal_tool(
    tool_name: str,
    args: dict[str, Any],
    auth_token: str,
) -> str:
    """Execute an internal tool (not in registry).

    Internal tools are system-level tools that have direct database access
    but are not part of the registry (not usable in workflows). They are
    used for specialized functionality like the builder assistant.

    Args:
        tool_name: The internal tool to execute.
        args: Arguments to pass to the tool
        auth_token: JWT token for authentication and authorization

    Returns:
        JSON-encoded result from the tool
    """
    try:
        claims = verify_mcp_token(auth_token)
    except ValueError as e:
        logger.warning("MCP token verification failed", error_type=_safe_error_type(e))
        raise ToolError("Authentication failed") from None

    _set_role_context(claims)
    # Validate tool is in allowed_internal_tools
    if tool_name not in claims.allowed_internal_tools:
        logger.warning(
            "Internal tool not authorized",
            tool_name=tool_name,
            allowed_internal_tools=claims.allowed_internal_tools,
        )
        raise ToolError(f"Tool '{tool_name}' not authorized")

    # Look up handler
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


app = mcp.http_app(path="/mcp")
