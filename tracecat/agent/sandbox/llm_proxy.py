"""LLM socket proxy for agent executor.

This module provides a Unix socket server that runs on the host side and
proxies HTTP traffic from the sandboxed runtime to the selected LLM backend.
The socket is mounted into NSJail so the runtime can reach the host-side
backend without direct network access.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterable, Callable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import orjson
from fastapi import HTTPException

from tracecat import config as app_config
from tracecat.agent.observability import get_load_tracker
from tracecat.logger import logger

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


def _get_or_create_trace_request_id(headers: dict[str, str]) -> str:
    """Return the incoming trace ID header or generate a new one."""
    for key, value in headers.items():
        if key.lower() == _TRACE_REQUEST_ID_HEADER and value:
            return value
    return str(uuid4())


class LLMSocketProxy:
    """Unix socket proxy that forwards HTTP traffic to the LLM gateway.

    Runs on the host side as part of the agent executor. The socket is
    mounted into the NSJail sandbox where the LLMBridge connects to it.
    """

    # Providers that bypass the managed LiteLLM gateway and talk directly
    # to a user-supplied endpoint.  For these we strip the internal auth
    # header and Anthropic-specific reasoning fields from the request.
    _BYPASS_PROVIDERS = frozenset({"litellm"})

    def __init__(
        self,
        socket_path: Path,
        litellm_url: str | None = None,
        on_error: Callable[[str], None] | None = None,
        model_provider: str | None = None,
    ):
        """Initialize the LLM socket proxy.

        Args:
            socket_path: Path where the Unix socket will be created.
            litellm_url: Managed LiteLLM service URL for shared-service runs.
            on_error: Callback invoked when an error (e.g., auth failure) is detected.
            model_provider: The agent's model provider name.  When the
                provider is a bypass provider (e.g. ``"litellm"``), the proxy
                automatically strips the Authorization header and
                Anthropic-specific reasoning fields before forwarding.
        """
        self.socket_path = socket_path
        self.litellm_url = (
            litellm_url.rstrip("/")
            if litellm_url is not None
            else app_config.TRACECAT__LITELLM_BASE_URL.rstrip("/")
        )
        self._server: asyncio.Server | None = None
        self._client: httpx.AsyncClient | None = None
        self._on_error = on_error
        self._error_emitted = False  # Only call callback once
        self._is_bypass = model_provider in self._BYPASS_PROVIDERS

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

    _REASONING_FIELDS = ("thinking", "reasoning_effort")

    @staticmethod
    def _strip_reasoning_fields_from_request(
        body: bytes, headers: dict[str, str]
    ) -> tuple[bytes, dict[str, str]]:
        """Remove adaptive thinking / reasoning fields from a JSON request body.

        Strips ``thinking`` and ``reasoning_effort`` top-level keys and returns
        the updated body with a corrected ``Content-Length`` header.
        """
        try:
            data = orjson.loads(body)
        except orjson.JSONDecodeError:
            return body, headers

        changed = False
        for field in LLMSocketProxy._REASONING_FIELDS:
            if field in data:
                del data[field]
                changed = True

        if not changed:
            return body, headers

        new_body = orjson.dumps(data)
        headers = {
            key: (str(len(new_body)) if key.lower() == "content-length" else value)
            for key, value in headers.items()
        }
        return new_body, headers

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
        request: dict[str, Any],
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

        path = str(request["path"])
        method = str(request["method"])
        headers = cast(dict[str, str], request["headers"])
        body = cast(bytes, request["body"])

        if self._is_bypass and body:
            body, headers = self._strip_reasoning_fields_from_request(body, headers)

        url = f"{self.litellm_url}{path}"
        excluded_headers = {"host", "connection", "transfer-encoding"}
        if self._is_bypass:
            excluded_headers.add("authorization")
        forward_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() not in excluded_headers
        }

        try:
            async with self._client.stream(
                method=method,
                url=url,
                headers=forward_headers,
                content=body if body else None,
            ) as response:
                await self._write_response(
                    writer,
                    status_code=response.status_code,
                    reason_phrase=response.reason_phrase,
                    headers=dict(response.headers),
                    body_chunks=response.aiter_bytes(),
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
            if path.split("?", 1)[0] not in _NON_CRITICAL_PATHS:
                self._emit_error(f"LiteLLM unavailable: {exc}")
        except httpx.TimeoutException as exc:
            await self._write_error_response(
                writer,
                status_code=504,
                detail="Gateway timeout",
                request_counter=request_counter,
                trace_request_id=trace_request_id,
            )
            if path.split("?", 1)[0] not in _NON_CRITICAL_PATHS:
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
