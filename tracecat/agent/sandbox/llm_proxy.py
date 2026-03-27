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

from tracecat.agent.litellm_observability import get_load_tracker
from tracecat.agent.llm_proxy.auth import verify_claims_from_headers
from tracecat.agent.llm_proxy.core import TracecatLLMProxy
from tracecat.config import TRACECAT__LLM_PROXY_READ_TIMEOUT
from tracecat.logger import logger

LITELLM_URL = "http://127.0.0.1:4000"
TRACECAT_PROXY_BASE_URL = "http://tracecat-llm-proxy"

# Socket filename (created in job's socket directory)
LLM_SOCKET_NAME = "llm.sock"

# Maximum request body size (10 MB) - prevents memory exhaustion DoS
MAX_BODY_SIZE = 10 * 1024 * 1024
_TRACECAT_PROXY_SHUTDOWN_GRACE_SECONDS = 5.0
_TRACECAT_PROXY_SHUTDOWN_POLL_SECONDS = 0.05

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
    """Unix socket proxy that forwards HTTP traffic to the selected LLM backend.

    Runs on the host side as part of the agent executor. The socket is
    mounted into the NSJail sandbox where the LLMBridge connects to it.
    """

    def __init__(
        self,
        socket_path: Path,
        litellm_url: str = LITELLM_URL,
        tracecat_proxy: TracecatLLMProxy | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        """Initialize the LLM socket proxy.

        Args:
            socket_path: Path where the Unix socket will be created.
            litellm_url: URL of the worker-global LiteLLM proxy.
            tracecat_proxy: In-process Tracecat proxy for execution-scoped runs.
            on_error: Callback invoked when an error (e.g., auth failure) is detected.
        """
        self.socket_path = socket_path
        self.litellm_url = litellm_url
        self.tracecat_proxy = tracecat_proxy
        self._server: asyncio.Server | None = None
        self._client: httpx.AsyncClient | None = None
        self._client_base_url = litellm_url
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
        timeout = httpx.Timeout(
            connect=20.0,
            read=TRACECAT__LLM_PROXY_READ_TIMEOUT,
            write=30.0,
            pool=10.0,
        )
        if self.tracecat_proxy is None:
            self._client_base_url = self.litellm_url
            self._client = httpx.AsyncClient(timeout=timeout)
        else:
            self._client_base_url = TRACECAT_PROXY_BASE_URL
            self._client = None

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
            backend=self._backend_name,
            backend_target=self._client_base_url,
            **_load_fields(),
        )

    async def stop(self) -> None:
        """Stop the Unix socket server and clean up."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        await self._wait_for_tracecat_proxy_requests()

        if self._client:
            await self._client.aclose()
            self._client = None
        if self.tracecat_proxy is not None:
            await self.tracecat_proxy.close()

        # Remove socket file
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass

        logger.info("LLM socket proxy stopped", backend=self._backend_name)

    @property
    def _backend_name(self) -> str:
        return "tracecat_proxy" if self.tracecat_proxy is not None else "litellm"

    async def _wait_for_tracecat_proxy_requests(self) -> None:
        if self.tracecat_proxy is None:
            return
        if self.tracecat_proxy.state.active_requests == 0:
            return

        logger.info(
            "Waiting for Tracecat proxy requests to finish before shutdown",
            active_requests=self.tracecat_proxy.state.active_requests,
        )
        deadline = time.monotonic() + _TRACECAT_PROXY_SHUTDOWN_GRACE_SECONDS
        while self.tracecat_proxy.state.active_requests > 0:
            if time.monotonic() >= deadline:
                logger.warning(
                    "Timed out waiting for Tracecat proxy requests to finish",
                    active_requests=self.tracecat_proxy.state.active_requests,
                )
                return
            await asyncio.sleep(_TRACECAT_PROXY_SHUTDOWN_POLL_SECONDS)

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
        """Forward an HTTP request to the selected backend and stream the response back.

        Handles both regular responses and streaming responses (SSE).
        """
        if self.tracecat_proxy is None and not self._client:
            self._emit_error("Proxy not initialized")
            return

        url = f"{self._client_base_url}{request['path']}"
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
            path_without_query = request["path"].split("?", 1)[0]
            if (
                self.tracecat_proxy is not None
                and path_without_query == "/api/event_logging/batch"
            ):
                await self._write_response(
                    writer,
                    status_code=204,
                    reason_phrase="No Content",
                    headers={"X-Request-ID": trace_request_id},
                    body_chunks=[],
                )
                return

            if self.tracecat_proxy is not None:
                await self._forward_tracecat_proxy_request(
                    writer=writer,
                    request=request,
                    trace_request_id=trace_request_id,
                    request_counter=request_counter,
                    started_at=started_at,
                )
                return

            client = self._client
            if client is None:
                self._emit_error("Proxy not initialized")
                return

            # Make the request to the backend with streaming
            async with client.stream(
                method=method,
                url=url,
                headers=forward_headers,
                content=body if body else None,
            ) as response:
                # Detect backend error responses
                if response.status_code >= 400:
                    is_non_critical = path_without_query in _NON_CRITICAL_PATHS

                    try:
                        error_body = await response.aread()
                        log_method = logger.warning if is_non_critical else logger.error
                        log_method(
                            "LLM backend error response",
                            request_counter=request_counter,
                            backend=self._backend_name,
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
                        logger.debug(
                            "LLM backend error body",
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
                    if not is_non_critical and self.tracecat_proxy is None:
                        self._emit_error(error_msg)
                    return

                await self._write_response(
                    writer,
                    status_code=response.status_code,
                    reason_phrase=response.reason_phrase,
                    headers=dict(response.headers),
                    body_chunks=response.aiter_bytes(),
                    trace_request_id=trace_request_id,
                    started_at=started_at,
                    request_counter=request_counter,
                    backend=self._backend_name,
                    method=method,
                    path=request["path"],
                )

        except httpx.ConnectError as e:
            error_category = _classify_httpx_exception(e)
            logger.error(
                "Failed to connect to LLM backend",
                request_counter=request_counter,
                backend=self._backend_name,
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
                detail="LLM backend unavailable",
                request_counter=request_counter,
                trace_request_id=trace_request_id,
            )
            if self.tracecat_proxy is None:
                self._emit_error("LLM backend unavailable")
        except httpx.TimeoutException as e:
            error_category = _classify_httpx_exception(e)
            logger.error(
                "LLM backend request timeout",
                request_counter=request_counter,
                backend=self._backend_name,
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
            if self.tracecat_proxy is None:
                self._emit_error("Gateway timeout")
        except httpx.ReadError:
            # Connection closed during request setup - check if client triggered it
            if writer.is_closing():
                logger.debug("Connection closed after client disconnect")
            else:
                logger.warning(
                    "LLM backend connection closed unexpectedly",
                    request_counter=request_counter,
                    backend=self._backend_name,
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
                "LLM proxy request finished",
                request_counter=request_counter,
                backend=self._backend_name,
                method=method,
                path=request["path"],
                trace_request_id=trace_request_id,
                elapsed_ms=(time.monotonic() - started_at) * 1000,
                active_proxy_requests=end_snapshot.active_requests,
            )

    async def _forward_tracecat_proxy_request(
        self,
        *,
        writer: asyncio.StreamWriter,
        request: dict[str, Any],
        trace_request_id: str,
        request_counter: int,
        started_at: float,
    ) -> None:
        if self.tracecat_proxy is None:
            raise RuntimeError("Tracecat proxy backend is not configured")

        path = str(request["path"])
        path_without_query = path.split("?", 1)[0]
        method = str(request["method"])
        headers = cast(dict[str, str], request["headers"])
        body = cast(bytes, request["body"])

        if method != "POST":
            await self._write_error_response(
                writer,
                status_code=405,
                detail="Method not allowed",
                request_counter=request_counter,
                trace_request_id=trace_request_id,
            )
            return

        try:
            claims = verify_claims_from_headers(headers)
        except ValueError as exc:
            await self._write_error_response(
                writer,
                status_code=401,
                detail=str(exc),
                request_counter=request_counter,
                trace_request_id=trace_request_id,
            )
            return

        if not body:
            payload: dict[str, Any] = {}
        else:
            try:
                decoded_payload = orjson.loads(body)
            except orjson.JSONDecodeError as exc:
                await self._write_error_response(
                    writer,
                    status_code=400,
                    detail=f"Malformed JSON in request body: {exc}",
                    request_counter=request_counter,
                    trace_request_id=trace_request_id,
                )
                return
            match decoded_payload:
                case dict() as parsed_payload:
                    payload = parsed_payload
                case _:
                    await self._write_error_response(
                        writer,
                        status_code=400,
                        detail="Request body must be a JSON object",
                        request_counter=request_counter,
                        trace_request_id=trace_request_id,
                    )
                    return

        try:
            match path_without_query:
                case "/v1/messages":
                    events = await self.tracecat_proxy.stream_messages(
                        payload=payload,
                        claims=claims,
                        trace_request_id=trace_request_id,
                    )
                    await self._write_response(
                        writer,
                        status_code=200,
                        reason_phrase="OK",
                        headers={"Content-Type": "text/event-stream"},
                        body_chunks=events,
                        trace_request_id=trace_request_id,
                        started_at=started_at,
                        request_counter=request_counter,
                        backend=self._backend_name,
                        method=method,
                        path=path,
                    )
                case "/v1/messages/count_tokens":
                    count_response = {
                        "type": "count_tokens",
                        "provider": claims.provider,
                        "model": claims.model,
                        "input_tokens": max(1, len(payload.get("messages", []))),
                    }
                    await self._write_response(
                        writer,
                        status_code=200,
                        reason_phrase="OK",
                        headers={"Content-Type": "application/json"},
                        body_chunks=[orjson.dumps(count_response)],
                        trace_request_id=trace_request_id,
                    )
                case _:
                    await self._write_error_response(
                        writer,
                        status_code=404,
                        detail="Not found",
                        request_counter=request_counter,
                        trace_request_id=trace_request_id,
                    )
        except HTTPException as exc:
            await self._write_error_response(
                writer,
                status_code=exc.status_code,
                detail=str(exc.detail),
                request_counter=request_counter,
                trace_request_id=trace_request_id,
            )

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
        backend: str | None = None,
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
                            backend=backend or self._backend_name,
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
