"""LLM socket proxy for agent executor.

This module provides a Unix socket server that runs on the host side and
proxies HTTP traffic to the LiteLLM proxy at localhost:4000. The socket
is mounted into the NSJail sandbox, allowing the sandboxed agent runtime
to communicate with LiteLLM without direct network access.

The proxy handles:
- HTTP/1.1 request parsing from the Unix socket
- Forwarding requests to LiteLLM via HTTP
- Streaming responses (SSE for LLM completions) back through the socket

Security:
- Socket permissions are set to 0o600 (owner only)
- No authentication at this layer (JWT auth happens at LiteLLM gateway)
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import httpx
import orjson

from tracecat.agent.litellm_observability import get_load_tracker
from tracecat.config import TRACECAT__LLM_PROXY_READ_TIMEOUT
from tracecat.logger import logger

# LiteLLM proxy runs on localhost:4000
LITELLM_URL = "http://127.0.0.1:4000"

# Socket filename (created in job's socket directory)
LLM_SOCKET_NAME = "llm.sock"

# Maximum request body size (10 MB) - prevents memory exhaustion DoS
MAX_BODY_SIZE = 10 * 1024 * 1024

# Non-critical endpoints that should not trigger fatal errors on failure.
# These are internal LiteLLM endpoints for telemetry, health checks, token
# counting, etc. Errors on these endpoints are logged but don't fail the agent.
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
_proxy_load_tracker = get_load_tracker("llm_socket_proxy")
_TRACE_REQUEST_ID_HEADER = "x-request-id"


def _load_fields() -> dict[str, int]:
    snapshot = _proxy_load_tracker.snapshot()
    return {
        "active_proxy_connections": snapshot.active_connections,
        "active_proxy_requests": snapshot.active_requests,
        "proxy_peak_active_connections": snapshot.peak_active_connections,
        "proxy_peak_active_requests": snapshot.peak_active_requests,
    }


def _classify_httpx_exception(exc: Exception) -> str:
    """Return the exception type name for logging and bucketing."""
    return type(exc).__name__


def _get_or_create_trace_request_id(headers: dict[str, str]) -> str:
    """Return the incoming trace ID header or generate a new one."""
    for key, value in headers.items():
        if key.lower() == _TRACE_REQUEST_ID_HEADER and value:
            return value
    return str(uuid4())


class LLMSocketProxy:
    """Unix socket proxy that forwards HTTP traffic to LiteLLM.

    Runs on the host side as part of the agent executor. The socket is
    mounted into the NSJail sandbox where the LLMBridge connects to it.
    """

    def __init__(
        self,
        socket_path: Path,
        litellm_url: str = LITELLM_URL,
        on_error: Callable[[str], None] | None = None,
    ):
        """Initialize the LLM socket proxy.

        Args:
            socket_path: Path where the Unix socket will be created.
            litellm_url: URL of the LiteLLM proxy (default: http://127.0.0.1:4000).
            on_error: Callback invoked when an error (e.g., auth failure) is detected.
        """
        self.socket_path = socket_path
        self.litellm_url = litellm_url
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
                connect=20.0,
                read=TRACECAT__LLM_PROXY_READ_TIMEOUT,
                write=30.0,
                pool=10.0,
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
            litellm_url=self.litellm_url,
            **_load_fields(),
        )

    async def stop(self) -> None:
        """Stop the Unix socket server and clean up."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self._client:
            await self._client.aclose()
            self._client = None

        # Remove socket file
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass

        logger.info("LLM socket proxy stopped")

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

        Reads HTTP requests and forwards them to LiteLLM, streaming
        responses back through the socket.
        """
        logger.debug("LLM proxy connection received")
        _proxy_load_tracker.begin_connection()

        try:
            # Parse the HTTP request
            request = await self._parse_http_request(reader)
            if not request:
                return

            # Forward to LiteLLM and stream response back
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
            closed_snapshot = _proxy_load_tracker.end_connection()
            logger.debug(
                "LLM proxy connection closed",
                active_proxy_connections=closed_snapshot.active_connections,
                active_proxy_requests=closed_snapshot.active_requests,
            )
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _parse_http_request(
        self,
        reader: asyncio.StreamReader,
    ) -> dict | None:
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
        request: dict,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Forward an HTTP request to LiteLLM and stream the response back.

        Handles both regular responses and streaming responses (SSE).
        """
        if not self._client:
            self._emit_error("Proxy not initialized")
            return

        url = f"{self.litellm_url}{request['path']}"
        method = request["method"]
        headers = request["headers"]
        body = request["body"]
        request_counter, _ = _proxy_load_tracker.begin_request()
        started_at = time.monotonic()

        # Remove hop-by-hop headers that shouldn't be forwarded
        forward_headers = {
            k: v
            for k, v in headers.items()
            if k.lower() not in ("host", "connection", "transfer-encoding")
        }
        trace_request_id = _get_or_create_trace_request_id(headers)
        forward_headers["X-Request-ID"] = trace_request_id

        try:
            # Make the request to LiteLLM with streaming
            async with self._client.stream(
                method=method,
                url=url,
                headers=forward_headers,
                content=body if body else None,
            ) as response:
                # Detect error responses from LiteLLM
                if response.status_code >= 400:
                    # Check if this is a non-critical endpoint (telemetry, health checks, etc.)
                    # Strip query string before matching (path may include ?beta=true etc.)
                    path_without_query = request["path"].split("?", 1)[0]
                    is_non_critical = path_without_query in _NON_CRITICAL_PATHS

                    # Read error body but only log metadata to avoid sensitive data leakage
                    try:
                        error_body = await response.aread()
                        log_method = logger.warning if is_non_critical else logger.error
                        log_method(
                            "LiteLLM error response",
                            request_counter=request_counter,
                            status_code=response.status_code,
                            method=method,
                            path=request["path"],
                            url=url,
                            response_length=len(error_body),
                            non_critical=is_non_critical,
                            trace_request_id=trace_request_id,
                            elapsed_ms=(time.monotonic() - started_at) * 1000,
                            **_load_fields(),
                        )
                        # Log full body only at debug level
                        logger.debug(
                            "LiteLLM error body",
                            response_body=error_body.decode("utf-8", errors="replace")[
                                :1000
                            ],
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to read error body",
                            status_code=response.status_code,
                            error=str(e),
                        )

                    # Only emit fatal error for critical endpoints
                    error_msg = _ERROR_MESSAGES.get(
                        response.status_code,
                        f"LLM request failed ({response.status_code})",
                    )
                    await self._write_error_response(
                        writer,
                        status_code=response.status_code,
                        detail=error_msg,
                        request_counter=request_counter,
                        trace_request_id=trace_request_id,
                    )
                    if not is_non_critical:
                        self._emit_error(error_msg)
                    return

                # Build response headers
                response_line = (
                    f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n"
                )
                try:
                    writer.write(response_line.encode())

                    # Forward response headers
                    for key, value in response.headers.items():
                        # Skip hop-by-hop headers
                        if key.lower() in (
                            "connection",
                            "keep-alive",
                            "transfer-encoding",
                        ):
                            continue
                        header_line = f"{key}: {value}\r\n"
                        writer.write(header_line.encode())
                    writer.write(f"X-Request-ID: {trace_request_id}\r\n".encode())

                    writer.write(b"\r\n")
                    await writer.drain()
                except Exception as e:
                    if self._is_client_disconnect_error(e) or writer.is_closing():
                        logger.debug("Client disconnected before response headers")
                        return
                    raise

                # Stream response body
                try:
                    async for chunk in response.aiter_bytes():
                        try:
                            writer.write(chunk)
                            await writer.drain()
                        except Exception as e:
                            if (
                                not self._is_client_disconnect_error(e)
                                and not writer.is_closing()
                            ):
                                raise
                            # Client disconnected - this is normal when sandbox exits
                            logger.debug(
                                "Client disconnected during response streaming"
                            )
                            return
                except httpx.ReadError:
                    # Upstream closed - only expected if client also disconnected
                    if writer.is_closing():
                        logger.debug(
                            "Upstream connection closed after client disconnect"
                        )
                    else:
                        logger.warning(
                            "Upstream connection closed unexpectedly during streaming"
                        )
                    return

        except httpx.ConnectError as e:
            error_category = _classify_httpx_exception(e)
            logger.error(
                "Failed to connect to LiteLLM",
                request_counter=request_counter,
                method=method,
                path=request["path"],
                error=str(e),
                error_class=f"{type(e).__module__}.{type(e).__qualname__}",
                error_category=error_category,
                trace_request_id=trace_request_id,
                elapsed_ms=(time.monotonic() - started_at) * 1000,
                **_load_fields(),
            )
            await self._write_error_response(
                writer,
                status_code=502,
                detail="LiteLLM unavailable",
                request_counter=request_counter,
                trace_request_id=trace_request_id,
            )
            self._emit_error("LiteLLM unavailable")
        except httpx.TimeoutException as e:
            error_category = _classify_httpx_exception(e)
            logger.error(
                "LiteLLM request timeout",
                request_counter=request_counter,
                method=method,
                path=request["path"],
                error=str(e) or error_category,
                error_class=f"{type(e).__module__}.{type(e).__qualname__}",
                error_category=error_category,
                trace_request_id=trace_request_id,
                elapsed_ms=(time.monotonic() - started_at) * 1000,
                **_load_fields(),
            )
            await self._write_error_response(
                writer,
                status_code=504,
                detail="Gateway timeout",
                request_counter=request_counter,
                trace_request_id=trace_request_id,
            )
            self._emit_error("Gateway timeout")
        except httpx.ReadError:
            # Connection closed during request setup - check if client triggered it
            if writer.is_closing():
                logger.debug("Connection closed after client disconnect")
            else:
                logger.warning(
                    "Upstream connection closed unexpectedly",
                    request_counter=request_counter,
                    method=method,
                    path=request["path"],
                    trace_request_id=trace_request_id,
                    elapsed_ms=(time.monotonic() - started_at) * 1000,
                    **_load_fields(),
                )
                await self._write_error_response(
                    writer,
                    status_code=502,
                    detail="LLM provider unavailable",
                    request_counter=request_counter,
                    trace_request_id=trace_request_id,
                )
        except Exception as e:
            if not self._is_client_disconnect_error(e) and not writer.is_closing():
                raise
            # Client disconnected - this is normal when sandbox exits
            logger.debug("Client disconnected during request forwarding")
        finally:
            end_snapshot = _proxy_load_tracker.end_request()
            logger.debug(
                "LiteLLM proxy request finished",
                request_counter=request_counter,
                method=method,
                path=request["path"],
                trace_request_id=trace_request_id,
                elapsed_ms=(time.monotonic() - started_at) * 1000,
                active_proxy_requests=end_snapshot.active_requests,
            )

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
