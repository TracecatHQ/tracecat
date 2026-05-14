"""LLM socket proxy for agent executor.

This module provides a Unix socket server that runs on the host side and
proxies HTTP traffic from the sandboxed runtime to the selected LLM backend.
The socket is mounted into NSJail so the runtime can reach the host-side
backend without direct network access.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, TypedDict
from uuid import uuid4

import httpx
import orjson
from fastapi import HTTPException

from tracecat import config as app_config
from tracecat.agent.observability import get_load_tracker
from tracecat.agent.service import AgentManagementService
from tracecat.auth.types import Role
from tracecat.logger import logger

# Strip a trailing "/vN" segment (with optional trailing slash) from a
# passthrough upstream URL. The contract for stored ``base_url`` is the
# OpenAI-compatible "/v1" form (so catalog discovery can hit
# ``{base_url}/models`` → ``/v1/models``). In passthrough mode the SDK
# clients (Claude Code SDK, pydantic-ai) emit fully-qualified paths like
# ``/v1/messages``, so we strip the version suffix from direct route ``base_url``
# to avoid producing ``/v1/v1/messages``, which the upstream rejects with
# a 404 "model not found".
_PASSTHROUGH_VERSION_SUFFIX_RE = re.compile(r"/v\d+/?$")

# Socket filename (created in job's socket directory)
LLM_SOCKET_NAME = "llm.sock"

# Maximum request body size (10 MB) - prevents memory exhaustion DoS
MAX_BODY_SIZE = 10 * 1024 * 1024

# Non-critical endpoints that should not trigger fatal errors on failure.
_NON_CRITICAL_PATHS = frozenset(
    {
        "/api/event_logging/batch",
        "/v1/messages/count_tokens",
    }
)

# User-friendly error messages by status code
_ERROR_MESSAGES = {
    400: "Invalid request to LLM provider",
    401: "Authentication failed - check your API credentials",
    403: "Access denied - check your API permissions",
    404: "Model not found - check your model configuration",
    429: "Rate limit exceeded - please try again later",
    500: "LLM provider internal error",
    502: "LLM provider unavailable",
    503: "LLM provider temporarily unavailable",
    504: "LLM provider request timed out",
    529: "LLM provider is overloaded - please try again shortly",
}
_ERROR_BODY_PREVIEW_BYTES = 2048
_proxy_load_tracker = get_load_tracker("llm_socket_proxy")
_TRACE_REQUEST_ID_HEADER = "x-request-id"
_ANTHROPIC_ONLY_FIELDS = (
    "anthropic_beta",
    "context_management",
    "output_config",
    "output_format",
)


class ParsedRequest(TypedDict):
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class LLMForwardRequest:
    """Prepared upstream request after route-specific handling.

    Attributes:
        url: Full upstream URL for the selected route.
        headers: Headers to send upstream.
        body: Request body to send upstream.
    """

    url: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class LLMRoute:
    """One upstream target the proxy can forward a request to.

    The executor builds logical routes from agent configs, materializes direct
    routes with credentials before proxy startup, then the socket proxy selects
    a route and asks it to prepare the outbound request.

    Attributes:
        base_url: Host root for the upstream. Direct routes are normalized to
            remove a trailing OpenAI version segment such as `/v1`.
        model_provider: Provider behind the route.
        upstream_model_name: Optional provider-facing model name to send
            upstream when the local route key is synthetic.
        mode: `managed` preserves managed gateway auth; `direct` applies
            passthrough auth behavior.
        catalog_id: Optional custom-provider catalog row used to resolve direct
            route credentials.
        authorization: Optional materialized Authorization header value. Hidden
            from repr so credentials are not logged through dataclass rendering.
        local_provider_cleanup: Whether this route can safely apply
            provider-specific body cleanup before forwarding. Managed fallback
            routes that may represent synthetic subagent models should defer
            that cleanup to LiteLLM.
    """

    base_url: str
    model_provider: str
    upstream_model_name: str | None = None
    mode: Literal["managed", "direct"] = "direct"
    catalog_id: uuid.UUID | None = None
    authorization: str | None = field(default=None, repr=False)
    local_provider_cleanup: bool = True

    @property
    def is_direct(self) -> bool:
        """Return whether this route bypasses the managed gateway.

        Returns:
            True when the route forwards directly to a custom provider.
        """
        return self.mode == "direct"

    async def resolve_api_key(self, svc: AgentManagementService) -> str | None:
        """Resolve the API key needed by this route.

        Managed routes use the sandbox's existing managed gateway token and do
        not resolve a route-level credential. Direct routes resolve the
        custom-provider API key from their catalog entry or the legacy workspace
        secret.

        Args:
            svc: Agent management service scoped to the execution role.

        Returns:
            API key for direct passthrough routes, or None when no route-level
            key is needed or available.
        """
        if not self.is_direct:
            return None
        if self.catalog_id is not None:
            creds = await svc.get_catalog_credentials(self.catalog_id)
        else:
            creds = await svc.get_runtime_provider_credentials(
                "custom-model-provider",
            )
            if creds is None:
                creds = await svc.get_workspace_provider_credentials(
                    "custom-model-provider",
                )
        if creds is None:
            return None
        return creds.get("CUSTOM_MODEL_PROVIDER_API_KEY") or None

    def forward_url(self, path: str) -> str:
        """Build the upstream URL for a sandbox request path.

        Args:
            path: Request path emitted by the sandbox SDK.

        Returns:
            Full upstream URL for this route.
        """
        return f"{self.base_url}{path}"

    def forward_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Build outbound headers for this route.

        Managed routes preserve the sandbox's managed gateway Authorization
        header. Direct routes remove that managed token; materialized direct
        routes add their resolved custom-provider Authorization header.

        Args:
            headers: Parsed inbound request headers from the sandbox.

        Returns:
            Headers to forward upstream.
        """
        excluded_headers = {"host", "connection", "transfer-encoding"}
        if self.is_direct:
            excluded_headers.add("authorization")
        return {
            key: value
            for key, value in headers.items()
            if key.lower() not in excluded_headers
        }

    def forward_body_and_headers(
        self,
        *,
        body: bytes,
        data: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[bytes, dict[str, str]]:
        """Build outbound body and headers for this route.

        Direct routes know the destination provider locally, so they can remove
        fields that only Anthropic accepts before forwarding to non-Anthropic
        providers. Managed routes only do this when the route represents a
        single known provider; synthetic gateway routes defer provider cleanup to
        LiteLLM.

        Args:
            body: Raw inbound request body.
            data: Parsed JSON body, if the request body was a JSON object.
            headers: Current outbound headers.

        Returns:
            Body bytes and headers to forward upstream.
        """
        if data is None:
            return body, headers

        forward_data = self._forward_data(data)
        if forward_data is None:
            return body, headers

        body = orjson.dumps(forward_data)
        headers = {
            key: value
            for key, value in headers.items()
            if key.lower() != "content-length"
        }
        headers["Content-Length"] = str(len(body))
        return body, headers

    def _forward_data(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """Return rewritten request JSON, or None when the original can pass through."""
        forward_data = dict(data)
        if self.upstream_model_name is not None:
            forward_data["model"] = self.upstream_model_name
        if self.local_provider_cleanup and self.model_provider != "anthropic":
            for field_name in _ANTHROPIC_ONLY_FIELDS:
                forward_data.pop(field_name, None)
        return forward_data if forward_data != data else None

    def prepare_forward_request(
        self,
        *,
        path: str,
        headers: dict[str, str],
        body: bytes,
        data: dict[str, Any] | None,
    ) -> LLMForwardRequest:
        """Prepare a sandbox request for this route's upstream.

        The proxy's job is to pick the route. The route's job is to shape the
        outbound URL, headers, auth, and provider-specific request body.

        Args:
            path: Request path emitted by the sandbox SDK.
            headers: Parsed inbound request headers from the sandbox.
            body: Raw inbound request body.
            data: Parsed JSON body, if the request body was a JSON object.

        Returns:
            Prepared request for the upstream HTTP client.
        """
        body, headers = self.forward_body_and_headers(
            body=body,
            data=data,
            headers=headers,
        )
        headers = self.forward_headers(headers)
        if self.is_direct and self.authorization:
            headers["Authorization"] = self.authorization
        return LLMForwardRequest(
            url=self.forward_url(path),
            headers=headers,
            body=body,
        )


@dataclass(frozen=True, slots=True)
class LLMRoutingPlan:
    """Route requests by exact model key, falling back to managed LiteLLM.

    The proxy does not know whether a model belongs to a root agent or a
    subagent. The executor converts agent configs into this table before the
    sandbox starts.

    Attributes:
        managed_route: Fallback route for every request model that does not
            have an exact direct route match.
        direct_routes: Direct passthrough routes keyed by exact request model.
    """

    managed_route: LLMRoute
    direct_routes: dict[str, LLMRoute]

    def __post_init__(self) -> None:
        # Direct routes are stored in catalog-friendly OpenAI-compatible form
        # (`.../v1`) but forwarded against SDK-emitted paths (`/v1/messages`).
        # Normalize them once when the plan is built so request forwarding does
        # not need to care about URL shape.
        object.__setattr__(
            self,
            "direct_routes",
            {
                model_key: _normalize_direct_route(route)
                for model_key, route in self.direct_routes.items()
            },
        )

    async def materialize(self, role: Role | None) -> LLMRoutingPlan:
        """Return a copy of this plan with direct-route credentials bound.

        Args:
            role: Role used to fetch custom provider API keys for direct routes.

        Returns:
            Routing plan containing routes ready for request forwarding.
        """
        direct_authorizations: dict[str, str | None] = {}

        if self.direct_routes and role is not None:
            async with AgentManagementService.with_session(role) as svc:
                for route_key, route in self.direct_routes.items():
                    # Credentials follow the selected route, not the root execution.
                    # This is what lets a passthrough root and passthrough subagent
                    # use different custom-provider catalog entries in one run.
                    api_key = await route.resolve_api_key(svc)
                    authorization = f"Bearer {api_key}" if api_key else None
                    if authorization is None:
                        logger.warning(
                            "Passthrough credentials not found",
                            route_key=route_key,
                            catalog_id=(
                                str(route.catalog_id) if route.catalog_id else None
                            ),
                        )
                    else:
                        logger.info(
                            "Resolved passthrough upstream credentials",
                            route_key=route_key,
                            has_upstream_api_key=True,
                            catalog_id=(
                                str(route.catalog_id) if route.catalog_id else None
                            ),
                        )
                    direct_authorizations[route_key] = authorization
        elif self.direct_routes:
            for route_key, route in self.direct_routes.items():
                logger.warning(
                    "Passthrough credentials not found",
                    route_key=route_key,
                    catalog_id=str(route.catalog_id) if route.catalog_id else None,
                )
                direct_authorizations[route_key] = None

        return LLMRoutingPlan(
            managed_route=replace(self.managed_route, authorization=None),
            direct_routes={
                route_key: replace(
                    route, authorization=direct_authorizations.get(route_key)
                )
                for route_key, route in self.direct_routes.items()
            },
        )

    def resolve(self, request_model: object) -> LLMRoute:
        """Return the route for one request model.

        Args:
            request_model: Raw model value from the request body, if present.

        Returns:
            Route for request forwarding.
        """
        if isinstance(request_model, str) and (
            route := self.direct_routes.get(request_model)
        ):
            return route
        return self.managed_route


def _normalize_passthrough_base_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    return _PASSTHROUGH_VERSION_SUFFIX_RE.sub("", trimmed)


def _normalize_direct_route(route: LLMRoute) -> LLMRoute:
    """Normalize direct passthrough routes to host roots.

    Stored custom-provider URLs use OpenAI-compatible `/v1` form for catalog
    discovery. Runtime SDK requests already include `/v1/...` paths, so direct
    passthrough routes must strip the trailing version segment.

    Args:
        route: Direct route to normalize.

    Returns:
        Equivalent direct route with a normalized `base_url`.
    """
    return LLMRoute(
        base_url=_normalize_passthrough_base_url(route.base_url),
        model_provider=route.model_provider,
        upstream_model_name=route.upstream_model_name,
        mode="direct",
        catalog_id=route.catalog_id,
        authorization=route.authorization,
        local_provider_cleanup=route.local_provider_cleanup,
    )


def _load_fields() -> dict[str, int]:
    snapshot = _proxy_load_tracker.snapshot()
    return {
        "active_proxy_connections": snapshot.active_connections,
        "active_proxy_requests": snapshot.active_requests,
        "proxy_peak_active_connections": snapshot.peak_active_connections,
        "proxy_peak_active_requests": snapshot.peak_active_requests,
    }


def _get_or_create_trace_request_id(headers: dict[str, str]) -> str:
    """Return the incoming trace ID header or generate a new one."""
    for key, value in headers.items():
        if key.lower() == _TRACE_REQUEST_ID_HEADER and value:
            return value
    return str(uuid4())


def _is_non_critical_path(path: str) -> bool:
    return path.split("?", 1)[0] in _NON_CRITICAL_PATHS


def _coerce_error_detail(value: object) -> str | None:
    if isinstance(value, str):
        detail = value.strip()
        return detail or None
    if isinstance(value, dict):
        for key in ("message", "detail", "error"):
            if detail := _coerce_error_detail(value.get(key)):
                return detail
        return orjson.dumps(value).decode("utf-8")
    if isinstance(value, list):
        return orjson.dumps(value).decode("utf-8")
    return None


def _extract_error_detail(body: bytes) -> str | None:
    if not body:
        return None

    if len(body) <= _ERROR_BODY_PREVIEW_BYTES:
        try:
            parsed = orjson.loads(body)
        except orjson.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("error", "detail", "message"):
                if detail := _coerce_error_detail(parsed.get(key)):
                    return detail

    preview = body[:_ERROR_BODY_PREVIEW_BYTES].decode("utf-8", errors="replace")
    detail = preview.strip()
    if not detail:
        return None
    if len(body) > _ERROR_BODY_PREVIEW_BYTES:
        return f"{detail}..."
    return detail


def _format_litellm_http_error(
    *,
    status_code: int,
    reason_phrase: str,
    body: bytes,
    trace_request_id: str,
) -> str:
    reason = reason_phrase or "Unknown"
    message = _ERROR_MESSAGES.get(status_code)
    detail = _extract_error_detail(body)
    parts = [f"LiteLLM request failed ({status_code} {reason})"]
    if message:
        parts.append(message)
    if detail and detail != message:
        parts.append(detail)
    parts.append(f"request_id={trace_request_id}")
    return ": ".join(parts)


class LLMSocketProxy:
    """Unix socket proxy that forwards HTTP traffic to the LLM gateway.

    Runs on the host side as part of the agent executor. The socket is
    mounted into the NSJail sandbox where the LLMBridge connects to it.
    """

    def __init__(
        self,
        socket_path: Path,
        routing_plan: LLMRoutingPlan,
        on_error: Callable[[str], None] | None = None,
    ):
        """Initialize the LLM socket proxy.

        Args:
            socket_path: Path where the Unix socket will be created.
            routing_plan: Request routing plan. Direct passthrough routes should
                already have their route credentials materialized.
            on_error: Callback invoked when an error (e.g., auth failure) is detected.
        """
        self.socket_path = socket_path
        self.routing_plan = routing_plan
        self._server: asyncio.Server | None = None
        self._client: httpx.AsyncClient | None = None
        self._on_error = on_error
        self._error_emitted = False  # Only call callback once

    async def start(self) -> None:
        """Start the Unix socket server.

        Creates the socket file and begins accepting connections.
        The socket permissions are set to 0o600 for security.
        """
        # Ensure parent directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove existing socket file if present
        if self.socket_path.exists():
            self.socket_path.unlink()

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=app_config.TRACECAT__LLM_GATEWAY_CONNECT_TIMEOUT_SECONDS,
                read=app_config.TRACECAT__LLM_PROXY_READ_TIMEOUT,
                write=app_config.TRACECAT__LLM_GATEWAY_WRITE_TIMEOUT_SECONDS,
                pool=app_config.TRACECAT__LLM_GATEWAY_POOL_TIMEOUT_SECONDS,
            )
        )

        # Start Unix socket server
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.socket_path),
        )

        # Set socket permissions (owner only)
        os.chmod(self.socket_path, 0o600)

        logger.info(
            "LLM socket proxy started",
            socket_path=str(self.socket_path),
            **_load_fields(),
        )

    async def stop(self) -> None:
        """Stop the Unix socket server and clean up."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self._client is not None:
            await self._client.aclose()
            self._client = None

        # Remove socket file
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass

        logger.info("LLM socket proxy stopped")

    async def _iter_body_chunks(
        self,
        chunks: AsyncIterable[bytes] | list[bytes],
    ) -> AsyncIterable[bytes]:
        if isinstance(chunks, list):
            for chunk in chunks:
                yield chunk
            return
        async for chunk in chunks:
            yield chunk

    def _emit_error(self, message: str) -> None:
        """Emit error via callback (only once)."""
        if not self._error_emitted:
            self._error_emitted = True
            logger.error("LLM proxy error", error=message, **_load_fields())
            if self._on_error:
                self._on_error(message)

    @staticmethod
    def _is_client_disconnect_error(exc: Exception) -> bool:
        """Return True for expected writer-close errors during teardown."""
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return True
        if isinstance(exc, RuntimeError):
            message = str(exc).lower()
            return "handler is closed" in message or "transport closed" in message
        return False

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an incoming connection from the sandbox LLM bridge.

        Reads HTTP requests and forwards them to the selected backend, streaming
        responses back through the socket.
        """
        _proxy_load_tracker.begin_connection()

        try:
            # Parse the HTTP request
            request = await self._parse_http_request(reader)
            if not request:
                return

            # Forward to the selected backend and stream response back
            await self._forward_request(request, writer)

        except asyncio.IncompleteReadError:
            logger.debug("Client disconnected during request")
        except ConnectionError:
            # Transport closed during shutdown - not a fatal error
            logger.debug("Connection closed during proxy request")
        except Exception as e:
            if self._is_client_disconnect_error(e) or writer.is_closing():
                logger.debug("Client disconnected during proxy request")
                return
            # Don't emit fatal error if server is already shutting down
            if self._server is None:
                logger.debug("Proxy error during shutdown (ignored)", error=str(e))
            else:
                logger.exception("LLM proxy error", error=str(e))
                self._emit_error(f"Proxy error: {e}")
        finally:
            _proxy_load_tracker.end_connection()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _parse_http_request(
        self,
        reader: asyncio.StreamReader,
    ) -> ParsedRequest | None:
        """Parse an HTTP request from the socket.

        Returns:
            Dict with method, path, headers, and body, or None if connection closed.
        """
        # Read request line
        request_line = await reader.readline()
        if not request_line:
            return None

        try:
            request_line_str = request_line.decode("utf-8").strip()
            parts = request_line_str.split(" ", 2)
            if len(parts) < 2:
                self._emit_error("Malformed request line")
                return None
            method = parts[0]
            path = parts[1]
        except (UnicodeDecodeError, ValueError):
            self._emit_error("Invalid request encoding")
            return None

        # Read headers
        headers: dict[str, str] = {}
        content_length = 0
        while True:
            line = await reader.readline()
            if not line or line == b"\r\n":
                break
            try:
                header_str = line.decode("utf-8").strip()
                if ":" in header_str:
                    key, value = header_str.split(":", 1)
                    key = key.strip()
                    value = value.strip()
                    headers[key] = value
                    if key.lower() == "content-length":
                        content_length = int(value)
            except (UnicodeDecodeError, ValueError):
                continue

        # Validate content length to prevent memory exhaustion DoS
        if content_length > MAX_BODY_SIZE:
            logger.warning(
                "Request body too large",
                content_length=content_length,
                max_size=MAX_BODY_SIZE,
            )
            self._emit_error("Request body too large")
            return None

        # Read body if present
        body = b""
        if content_length > 0:
            body = await reader.readexactly(content_length)

        return {
            "method": method,
            "path": path,
            "headers": headers,
            "body": body,
        }

    async def _forward_request(
        self,
        request: ParsedRequest,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Forward an HTTP request to the LLM gateway and stream the response back."""
        method = request["method"]
        headers = request["headers"]
        request_counter, _ = _proxy_load_tracker.begin_request()
        started_at = time.monotonic()

        trace_request_id = _get_or_create_trace_request_id(headers)

        try:
            path_without_query = request["path"].split("?", 1)[0]
            if path_without_query == "/api/event_logging/batch":
                await self._write_response(
                    writer,
                    status_code=204,
                    reason_phrase="No Content",
                    headers={"X-Request-ID": trace_request_id},
                    body_chunks=[],
                )
                return

            await self._forward_http_backend_request(
                writer=writer,
                request=request,
                trace_request_id=trace_request_id,
                request_counter=request_counter,
                started_at=started_at,
            )
        except Exception as e:
            if not self._is_client_disconnect_error(e) and not writer.is_closing():
                raise
            # Client disconnected - this is normal when sandbox exits
            logger.debug("Client disconnected during request forwarding")
        finally:
            end_snapshot = _proxy_load_tracker.end_request()
            logger.debug(
                "LLM proxy request finished",
                request_counter=request_counter,
                method=method,
                path=request["path"],
                trace_request_id=trace_request_id,
                elapsed_ms=(time.monotonic() - started_at) * 1000,
                active_proxy_requests=end_snapshot.active_requests,
            )

    async def _forward_http_backend_request(
        self,
        *,
        writer: asyncio.StreamWriter,
        request: ParsedRequest,
        trace_request_id: str,
        request_counter: int,
        started_at: float,
    ) -> None:
        if self._client is None:
            await self._write_error_response(
                writer,
                status_code=503,
                detail="LiteLLM proxy not initialized",
                request_counter=request_counter,
                trace_request_id=trace_request_id,
            )
            self._emit_error("LiteLLM proxy not initialized")
            return

        path = request["path"]
        method = request["method"]
        headers = request["headers"]
        body = request["body"]

        data: dict[str, Any] | None = None
        if body:
            try:
                parsed = orjson.loads(body)
            except orjson.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                data = parsed

        route = self.routing_plan.resolve(
            data.get("model") if data is not None else None
        )
        upstream_request = route.prepare_forward_request(
            path=path,
            headers=headers,
            body=body,
            data=data,
        )

        try:
            async with self._client.stream(
                method=method,
                url=upstream_request.url,
                headers=upstream_request.headers,
                content=upstream_request.body if upstream_request.body else None,
            ) as response:
                body_chunks: AsyncIterable[bytes] | list[bytes]
                if response.status_code >= 400 and not _is_non_critical_path(path):
                    error_body = await response.aread()
                    self._emit_error(
                        _format_litellm_http_error(
                            status_code=response.status_code,
                            reason_phrase=response.reason_phrase,
                            body=error_body,
                            trace_request_id=trace_request_id,
                        )
                    )
                    body_chunks = [error_body]
                else:
                    body_chunks = response.aiter_bytes()

                await self._write_response(
                    writer,
                    status_code=response.status_code,
                    reason_phrase=response.reason_phrase,
                    headers=dict(response.headers),
                    body_chunks=body_chunks,
                    trace_request_id=trace_request_id,
                    started_at=started_at,
                    request_counter=request_counter,
                    method=method,
                    path=path,
                )
        except httpx.ConnectError as exc:
            await self._write_error_response(
                writer,
                status_code=502,
                detail="LiteLLM unavailable",
                request_counter=request_counter,
                trace_request_id=trace_request_id,
            )
            if not _is_non_critical_path(path):
                self._emit_error(f"LiteLLM unavailable: {exc}")
        except httpx.TimeoutException as exc:
            await self._write_error_response(
                writer,
                status_code=504,
                detail="Gateway timeout",
                request_counter=request_counter,
                trace_request_id=trace_request_id,
            )
            if not _is_non_critical_path(path):
                self._emit_error(f"Gateway timeout ({type(exc).__name__}): {exc}")

    async def _write_response(
        self,
        writer: asyncio.StreamWriter,
        *,
        status_code: int,
        reason_phrase: str,
        headers: dict[str, str],
        body_chunks: AsyncIterable[bytes] | list[bytes],
        trace_request_id: str | None = None,
        started_at: float | None = None,
        request_counter: int | None = None,
        method: str | None = None,
        path: str | None = None,
    ) -> None:
        """Write an HTTP response head and stream the response body."""
        content_type = next(
            (value for key, value in headers.items() if key.lower() == "content-type"),
            None,
        )
        is_streaming_response = (
            content_type is not None and "text/event-stream" in content_type.lower()
        )
        ttft_logged = False
        response_line = f"HTTP/1.1 {status_code} {reason_phrase}\r\n"
        try:
            writer.write(response_line.encode())
            for key, value in headers.items():
                if key.lower() in ("connection", "keep-alive", "transfer-encoding"):
                    continue
                writer.write(f"{key}: {value}\r\n".encode())
            if trace_request_id is not None:
                writer.write(f"X-Request-ID: {trace_request_id}\r\n".encode())
            writer.write(b"\r\n")
            await writer.drain()
        except Exception as exc:
            if self._is_client_disconnect_error(exc) or writer.is_closing():
                logger.debug("Client disconnected before response headers")
                return
            raise

        try:
            async for chunk in self._iter_body_chunks(body_chunks):
                try:
                    if (
                        is_streaming_response
                        and not ttft_logged
                        and chunk
                        and started_at is not None
                    ):
                        ttft_logged = True
                        logger.info(
                            "LLM proxy first response chunk",
                            request_counter=request_counter,
                            method=method,
                            path=path,
                            trace_request_id=trace_request_id,
                            ttft_ms=(time.monotonic() - started_at) * 1000,
                        )
                    writer.write(chunk)
                    await writer.drain()
                except Exception as exc:
                    if (
                        not self._is_client_disconnect_error(exc)
                        and not writer.is_closing()
                    ):
                        raise
                    logger.debug("Client disconnected during response streaming")
                    return
        except Exception as exc:
            # Headers (200 OK) are already flushed — we cannot write a
            # second HTTP error response.  Emit the error as an SSE event
            # so the client can surface it.  This covers HTTPException from
            # the proxy layer and RuntimeError from upstream provider errors
            # (e.g. _raise_stream_http_error on 4xx/5xx).
            if is_streaming_response and not writer.is_closing():
                status_code = getattr(exc, "status_code", 502)
                detail = (
                    str(exc.detail)
                    if isinstance(exc, HTTPException)
                    else str(exc)[:512]
                )
                logger.warning(
                    "Stream error after headers sent, emitting SSE error event",
                    status_code=status_code,
                    detail=detail,
                    trace_request_id=trace_request_id,
                )
                if path is not None and not _is_non_critical_path(path):
                    self._emit_error(f"LiteLLM stream failed: {detail}")
                error_payload = orjson.dumps(
                    {
                        "type": "error",
                        "error": {
                            "type": "server_error",
                            "message": detail,
                        },
                    }
                )
                try:
                    writer.write(b"event: error\ndata: " + error_payload + b"\n\n")
                    await writer.drain()
                except Exception:
                    logger.debug(
                        "Failed to send SSE error event, client likely disconnected"
                    )
            else:
                raise

    async def _write_error_response(
        self,
        writer: asyncio.StreamWriter,
        *,
        status_code: int,
        detail: str,
        request_counter: int,
        trace_request_id: str,
    ) -> None:
        """Write a synthetic JSON error response back to the sandbox client."""
        if writer.is_closing():
            return
        body = orjson.dumps(
            {
                "detail": detail,
                "status_code": status_code,
                "request_counter": request_counter,
                "trace_request_id": trace_request_id,
            }
        )
        reason = _ERROR_MESSAGES.get(status_code, detail)
        response_head = (
            f"HTTP/1.1 {status_code} {reason}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"X-Request-ID: {trace_request_id}\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        writer.write(response_head.encode("utf-8") + body)
        await writer.drain()
