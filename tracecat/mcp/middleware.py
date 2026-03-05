"""Custom MCP server middleware for input validation and timeouts."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from typing import Any, Protocol, TypedDict, cast

import mcp.types as mt
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token, get_http_request
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult

from tracecat.logger import logger
from tracecat.mcp.auth import MCPTokenIdentity, get_token_identity
from tracecat.mcp.config import (
    TRACECAT_MCP__MAX_INPUT_SIZE_BYTES,
    TRACECAT_MCP__TOOL_TIMEOUT_SECONDS,
)


class AccessTokenClaims(TypedDict, total=False):
    email: str
    client_id: str
    azp: str
    sub: str


class AccessTokenLike(Protocol):
    claims: AccessTokenClaims
    client_id: str


class FastMCPContextLike(Protocol):
    def get_access_token(self) -> AccessTokenLike | None: ...


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
                    size = len(value.encode("utf-8"))
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


class WatchtowerMonitorMiddleware(Middleware):
    """Persist local-agent initialize and tool-call telemetry for Watchtower."""

    async def on_initialize(
        self,
        context: MiddlewareContext[mt.InitializeRequest],
        call_next: CallNext[mt.InitializeRequest, mt.InitializeResult | None],
    ) -> mt.InitializeResult | None:
        result = await call_next(context)
        identity = _safe_get_token_identity()
        if identity is None or identity.email is None:
            return result

        session_id = _extract_mcp_session_id(context)
        if session_id is None:
            return result

        params = context.message.params
        if params is None:
            return result

        client_info = _normalize_client_info(params.clientInfo)
        user_agent = _request_header("user-agent")
        try:
            from tracecat_ee.watchtower.service import (
                ingest_watchtower_initialize_event,
            )

            await ingest_watchtower_initialize_event(
                email=identity.email,
                auth_client_id=identity.client_id,
                mcp_session_id=session_id,
                user_agent=user_agent,
                client_info=client_info,
                claimed_org_ids=identity.organization_ids or None,
                claimed_workspace_ids=identity.workspace_ids or None,
            )
        except Exception as exc:
            logger.warning(
                "Failed to ingest Watchtower initialize event", error=str(exc)
            )
        return result

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        identity = _safe_get_token_identity()
        if identity is None or identity.email is None:
            return await call_next(context)

        session_id = _extract_mcp_session_id(context)
        if session_id is None:
            return await call_next(context)

        try:
            from tracecat_ee.watchtower.service import (
                get_watchtower_tool_call_context,
                record_watchtower_tool_call,
            )
        except Exception:
            return await call_next(context)

        try:
            call_context, block_reason = await get_watchtower_tool_call_context(
                email=identity.email,
                mcp_session_id=session_id,
                claimed_org_ids=identity.organization_ids or None,
                claimed_workspace_ids=identity.workspace_ids or None,
            )
        except Exception as exc:
            logger.warning("Failed to resolve Watchtower call context", error=str(exc))
            return await call_next(context)

        workspace_id = _resolve_workspace_id(
            identity=identity,
            arguments=context.message.arguments,
        )

        if block_reason is not None:
            if call_context is not None:
                try:
                    await record_watchtower_tool_call(
                        call_context=call_context,
                        tool_name=context.message.name,
                        call_status="blocked",
                        latency_ms=0,
                        workspace_id=workspace_id,
                        tool_args=_tool_args_for_storage(context.message.arguments),
                        error_redacted=block_reason,
                        email=identity.email,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to persist Watchtower blocked tool call",
                        error=str(exc),
                    )
            raise ToolError(block_reason)

        if call_context is None:
            return await call_next(context)

        started = time.monotonic()
        call_status = "success"
        error_redacted: str | None = None

        try:
            result = await call_next(context)
            return result
        except ToolError as exc:
            error_redacted = str(exc)
            call_status = _derive_tool_call_status(error_redacted)
            raise
        except Exception as exc:
            error_redacted = str(exc)
            call_status = "error"
            raise
        finally:
            latency_ms = int((time.monotonic() - started) * 1000)
            try:
                await record_watchtower_tool_call(
                    call_context=call_context,
                    tool_name=context.message.name,
                    call_status=call_status,
                    latency_ms=latency_ms,
                    workspace_id=workspace_id,
                    tool_args=_tool_args_for_storage(context.message.arguments),
                    error_redacted=error_redacted,
                    email=identity.email,
                )
            except Exception as exc:
                logger.warning("Failed to persist Watchtower tool call", error=str(exc))


def _safe_get_token_identity() -> MCPTokenIdentity | None:
    try:
        return get_token_identity()
    except Exception:
        return None


def _extract_mcp_session_id(context: MiddlewareContext[Any]) -> str | None:
    fastmcp_context = context.fastmcp_context
    if fastmcp_context is not None:
        try:
            session_id = fastmcp_context.session_id
            if session_id:
                return str(session_id)
        except Exception:
            pass
    session_header = _request_header("mcp-session-id")
    if session_header:
        return session_header
    return None


def _normalize_client_info(client_info: object) -> dict[str, Any] | None:
    if client_info is None:
        return None
    model_dump = getattr(client_info, "model_dump", None)
    if callable(model_dump):
        payload = model_dump()
        if isinstance(payload, dict):
            return cast(dict[str, Any], payload)
    if isinstance(client_info, Mapping):
        return {str(key): value for key, value in client_info.items()}
    return None


def _request_header(name: str) -> str | None:
    try:
        request = get_http_request()
        value = request.headers.get(name)
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
    except Exception:
        return None


def _coerce_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _resolve_workspace_id(
    *,
    identity: MCPTokenIdentity,
    arguments: dict[str, object] | None,
) -> uuid.UUID | None:
    if len(identity.workspace_ids) == 1:
        return next(iter(identity.workspace_ids))
    if not arguments:
        return None

    for key in ("workspace_id", "workspaceId"):
        workspace_id = _coerce_uuid(arguments.get(key))
        if workspace_id is not None:
            return workspace_id
    return None


def _tool_args_for_storage(
    arguments: dict[str, object] | None,
) -> Mapping[str, Any] | None:
    if not arguments:
        return None
    return {str(key): value for key, value in arguments.items()}


def _derive_tool_call_status(error_message: str) -> str:
    lowered = error_message.lower()
    if "timed out" in lowered:
        return "timeout"
    if "blocked" in lowered:
        return "blocked"
    if "denied" in lowered or "forbidden" in lowered or "unauthorized" in lowered:
        return "rejected"
    return "error"


def get_mcp_client_id(context: MiddlewareContext) -> str:  # type: ignore[type-arg]
    """Extract a per-user client ID from the MCP middleware context.

    Uses the authenticated user's email from the OIDC access token.
    Falls back to 'anonymous' if the token is unavailable.
    """
    _ = context
    try:
        access_token: AccessTokenLike | None = None
        fastmcp_context = cast(
            FastMCPContextLike | None,
            getattr(context, "fastmcp_context", None),
        )
        if fastmcp_context is not None:
            access_token = fastmcp_context.get_access_token()
        if access_token is None:
            access_token = cast(AccessTokenLike | None, get_access_token())
        if access_token is not None:
            email = access_token.claims.get("email")
            if email:
                return str(email)
            client_id = (
                access_token.claims.get("client_id")
                or access_token.claims.get("azp")
                or access_token.claims.get("sub")
                or access_token.client_id
            )
            if client_id:
                return str(client_id)
    except Exception:
        logger.debug("Could not extract client ID from MCP context")
    return "anonymous"
