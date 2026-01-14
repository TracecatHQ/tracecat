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
from pathlib import Path

import httpx
import orjson

from tracecat.logger import logger

# LiteLLM proxy runs on localhost:4000
LITELLM_URL = "http://127.0.0.1:4000"

# Socket filename (created in job's socket directory)
LLM_SOCKET_NAME = "llm.sock"


class LLMSocketProxy:
    """Unix socket proxy that forwards HTTP traffic to LiteLLM.

    Runs on the host side as part of the agent executor. The socket is
    mounted into the NSJail sandbox where the LLMBridge connects to it.
    """

    def __init__(
        self,
        socket_path: Path,
        litellm_url: str = LITELLM_URL,
    ):
        """Initialize the LLM socket proxy.

        Args:
            socket_path: Path where the Unix socket will be created.
            litellm_url: URL of the LiteLLM proxy (default: http://127.0.0.1:4000).
        """
        self.socket_path = socket_path
        self.litellm_url = litellm_url
        self._server: asyncio.Server | None = None
        self._client: httpx.AsyncClient | None = None

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

        # Create httpx client for forwarding requests
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(600.0))

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

        try:
            # Parse the HTTP request
            request = await self._parse_http_request(reader)
            if not request:
                return

            # Forward to LiteLLM and stream response back
            await self._forward_request(request, writer)

        except asyncio.IncompleteReadError:
            logger.debug("Client disconnected during request")
        except Exception as e:
            logger.error("LLM proxy error", error=str(e))
            try:
                await self._send_error_response(writer, 500, "Internal proxy error")
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _parse_http_request(self, reader: asyncio.StreamReader) -> dict | None:
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
                return None
            method = parts[0]
            path = parts[1]
        except (UnicodeDecodeError, ValueError):
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
            await self._send_error_response(writer, 503, "Proxy not initialized")
            return

        url = f"{self.litellm_url}{request['path']}"
        method = request["method"]
        headers = request["headers"]
        body = request["body"]

        # Remove hop-by-hop headers that shouldn't be forwarded
        forward_headers = {
            k: v
            for k, v in headers.items()
            if k.lower() not in ("host", "connection", "transfer-encoding")
        }

        try:
            # Make the request to LiteLLM with streaming
            async with self._client.stream(
                method=method,
                url=url,
                headers=forward_headers,
                content=body if body else None,
            ) as response:
                # Build response headers
                response_line = (
                    f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n"
                )
                writer.write(response_line.encode())

                # Forward response headers
                for key, value in response.headers.items():
                    # Skip hop-by-hop headers
                    if key.lower() in ("connection", "keep-alive", "transfer-encoding"):
                        continue
                    header_line = f"{key}: {value}\r\n"
                    writer.write(header_line.encode())

                writer.write(b"\r\n")
                await writer.drain()

                # Stream response body
                async for chunk in response.aiter_bytes():
                    try:
                        writer.write(chunk)
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        # Client disconnected - this is normal when sandbox exits
                        logger.debug("Client disconnected during response streaming")
                        return

        except httpx.ConnectError as e:
            logger.error("Failed to connect to LiteLLM", error=str(e))
            await self._send_error_response(writer, 502, "LiteLLM unavailable")
        except httpx.TimeoutException as e:
            logger.error("LiteLLM request timeout", error=str(e))
            await self._send_error_response(writer, 504, "Gateway timeout")
        except (ConnectionResetError, BrokenPipeError):
            # Client disconnected - this is normal when sandbox exits
            logger.debug("Client disconnected during request forwarding")

    async def _send_error_response(
        self,
        writer: asyncio.StreamWriter,
        status_code: int,
        message: str,
    ) -> None:
        """Send an HTTP error response through the socket."""
        status_messages = {
            500: "Internal Server Error",
            502: "Bad Gateway",
            503: "Service Unavailable",
            504: "Gateway Timeout",
        }
        status_text = status_messages.get(status_code, "Error")
        body = orjson.dumps({"error": message}).decode()
        response = (
            f"HTTP/1.1 {status_code} {status_text}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{body}"
        )
        writer.write(response.encode())
        await writer.drain()
