"""Custom MCP server middleware for input validation and timeouts."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.middleware import Middleware

from tracecat.logger import logger
from tracecat.mcp.config import (
    TRACECAT_MCP__MAX_INPUT_SIZE_BYTES,
    TRACECAT_MCP__TOOL_TIMEOUT_SECONDS,
)

if TYPE_CHECKING:
    import mcp.types as mt
    from fastmcp.server.middleware.middleware import CallNext, MiddlewareContext
    from fastmcp.tools.tool import ToolResult


class MCPInputSizeLimitMiddleware(Middleware):
    """Reject tool calls where any string argument exceeds a byte-size limit.

    Prevents abuse via oversized YAML/JSON payloads sent as tool arguments.
    """

    def __init__(self, max_bytes: int = TRACECAT_MCP__MAX_INPUT_SIZE_BYTES) -> None:
        self.max_bytes = max_bytes

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        arguments = context.message.arguments
        if arguments:
            for key, value in arguments.items():
                if isinstance(value, str):
                    size = sys.getsizeof(value)
                    if size > self.max_bytes:
                        logger.warning(
                            "Tool call argument exceeds size limit",
                            tool=context.message.name,
                            argument=key,
                            size_bytes=size,
                            max_bytes=self.max_bytes,
                        )
                        raise ToolError(
                            f"Argument '{key}' exceeds maximum size "
                            f"({size} bytes > {self.max_bytes} bytes)"
                        )
        return await call_next(context)


class MCPTimeoutMiddleware(Middleware):
    """Wrap tool execution in an asyncio timeout.

    Raises ToolError if a tool call exceeds the configured timeout.
    """

    def __init__(
        self, timeout_seconds: int = TRACECAT_MCP__TOOL_TIMEOUT_SECONDS
    ) -> None:
        self.timeout_seconds = timeout_seconds

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        try:
            return await asyncio.wait_for(
                call_next(context), timeout=self.timeout_seconds
            )
        except TimeoutError:
            logger.warning(
                "Tool call timed out",
                tool=context.message.name,
                timeout_seconds=self.timeout_seconds,
            )
            raise ToolError(
                f"Tool '{context.message.name}' timed out "
                f"after {self.timeout_seconds} seconds"
            ) from None


def get_mcp_client_id(context: MiddlewareContext) -> str:  # type: ignore[type-arg]
    """Extract a per-user client ID from the MCP middleware context.

    Uses the authenticated user's email from the OIDC access token.
    Falls back to 'anonymous' if the token is unavailable.
    """
    _ = context
    try:
        access_token = get_access_token()
        if access_token is not None:
            email = access_token.claims.get("email")
            if email:
                return str(email)
    except Exception:
        logger.debug("Could not extract client ID from MCP context")
    return "anonymous"
