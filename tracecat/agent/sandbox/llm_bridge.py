"""LLM HTTP bridge for sandboxed agent runtime.

This module provides a pure Python HTTP bridge that runs inside the NSJail sandbox.
It binds to localhost and forwards all HTTP traffic to a Unix socket,
enabling the Claude SDK to communicate with LiteLLM without network access.

The bridge handles:
- HTTP/1.1 request parsing and forwarding
- Streaming responses (SSE for LLM completions)
- Connection keepalive

Port allocation:
- NSJail mode: Uses fixed port 4000 (isolated by network namespace)
- Direct mode: Uses port=0 for OS-assigned dynamic port (avoids clashes
  between concurrent agent runs sharing the host network namespace)

Usage:
    # NSJail mode (fixed port)
    bridge = LLMBridge(port=4000)
    await bridge.start()

    # Direct mode (dynamic port)
    bridge = LLMBridge(port=0)
    port = await bridge.start()  # Returns actual port
    # Claude SDK connects to http://127.0.0.1:{port}
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import orjson

from tracecat.logger import logger

# Well-known socket path inside the sandbox
JAILED_LLM_SOCKET_PATH = Path("/var/run/tracecat/llm.sock")

# Bridge listens on this address inside the sandbox
LLM_BRIDGE_HOST = "127.0.0.1"
# Default port for NSJail mode - port 4000 matches the gateway port
# In direct mode, use port=0 for OS-assigned dynamic port
LLM_BRIDGE_DEFAULT_PORT = 4000

# Maximum request body size (10 MB) - prevents memory exhaustion
MAX_BODY_SIZE = 10 * 1024 * 1024


class LLMBridge:
    """HTTP bridge that forwards localhost to Unix socket.

    Runs inside the NSJail sandbox to enable HTTP communication with
    the LiteLLM proxy on the host via a mounted Unix socket.

    Supports dynamic port allocation for direct mode to avoid port
    clashes between concurrent agent runs.
    """

    def __init__(
        self,
        socket_path: Path = JAILED_LLM_SOCKET_PATH,
        port: int = LLM_BRIDGE_DEFAULT_PORT,
    ):
        """Initialize the LLM bridge.

        Args:
            socket_path: Path to the Unix socket for LLM proxy communication.
            port: Port to listen on. Use 0 for OS-assigned dynamic port
                (recommended for direct mode to avoid clashes).
        """
        self.socket_path = socket_path
        self._requested_port = port
        self._actual_port: int | None = None
        self._server: asyncio.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None

    @property
    def port(self) -> int:
        """Get the actual port the bridge is listening on.

        Returns the OS-assigned port if started with port=0,
        otherwise returns the requested port.
        """
        return (
            self._actual_port if self._actual_port is not None else self._requested_port
        )

    async def start(self) -> int:
        """Start the HTTP bridge server.

        The server runs in the background and handles connections
        until stop() is called.

        Returns:
            The actual port the bridge is listening on (useful when
            started with port=0 for dynamic allocation).
        """
        self._server = await asyncio.start_server(
            self._handle_connection,
            host=LLM_BRIDGE_HOST,
            port=self._requested_port,
        )
        # Get the actual port from the server socket (important for port=0)
        actual_port = self._server.sockets[0].getsockname()[1]
        self._actual_port = actual_port

        # Start serving in the background with error handling
        # Store task as instance attribute to prevent garbage collection
        self._serve_task = asyncio.create_task(self._server.serve_forever())
        self._serve_task.add_done_callback(self._on_serve_done)
        logger.info(
            "LLM bridge started",
            host=LLM_BRIDGE_HOST,
            requested_port=self._requested_port,
            actual_port=actual_port,
            socket_path=str(self.socket_path),
        )
        return actual_port

    def _on_serve_done(self, task: asyncio.Task[None]) -> None:
        """Callback to log errors from serve_forever task."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("LLM bridge server failed", error=str(exc))

    async def stop(self) -> None:
        """Stop the HTTP bridge server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._serve_task = None
            logger.info("LLM bridge stopped")

    async def _handle_connection(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an incoming HTTP connection from the Claude SDK.

        Reads the HTTP request, forwards it to the Unix socket,
        and streams the response back to the client.
        """
        peer = client_writer.get_extra_info("peername")
        logger.debug("LLM bridge connection", peer=peer)

        try:
            # Read the complete HTTP request
            request_data = await self._read_http_request(client_reader)
            if not request_data:
                return

            # Connect to the Unix socket and forward the request
            try:
                sock_reader, sock_writer = await asyncio.open_unix_connection(
                    str(self.socket_path)
                )
            except (OSError, ConnectionRefusedError) as e:
                logger.error("Failed to connect to LLM socket", error=str(e))
                await self._send_error_response(
                    client_writer, 502, "LLM proxy unavailable"
                )
                return

            try:
                # Forward request to socket
                sock_writer.write(request_data)
                await sock_writer.drain()

                # Stream response back to client
                await self._stream_response(sock_reader, client_writer)

            finally:
                sock_writer.close()
                await sock_writer.wait_closed()

        except asyncio.IncompleteReadError:
            logger.debug("Client disconnected during request")
        except Exception as e:
            logger.exception("LLM bridge error", error=str(e))
            try:
                await self._send_error_response(
                    client_writer, 500, "Internal bridge error"
                )
            except Exception:
                pass
        finally:
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass

    async def _read_http_request(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read a complete HTTP request including headers and body.

        Handles Content-Length for request bodies (used in POST requests
        to LiteLLM for chat completions).

        Returns:
            Complete HTTP request as bytes, or None if connection closed.
        """
        # Read request line and headers
        headers_data = b""
        while True:
            line = await reader.readline()
            if not line:
                return None  # Connection closed
            headers_data += line
            if line == b"\r\n":
                break

        # Parse Content-Length if present
        content_length = 0
        for line in headers_data.split(b"\r\n"):
            lower_line = line.lower()
            if lower_line.startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
                break

        # Validate content length to prevent memory exhaustion
        if content_length > MAX_BODY_SIZE:
            logger.warning(
                "Request body too large",
                content_length=content_length,
                max_size=MAX_BODY_SIZE,
            )
            return None

        # Read body if present
        body = b""
        if content_length > 0:
            body = await reader.readexactly(content_length)

        return headers_data + body

    async def _stream_response(
        self,
        sock_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Stream HTTP response from socket to client.

        Handles both regular responses and streaming responses (SSE)
        used for LLM completions.
        """
        # Read and forward response in chunks for low latency streaming
        while True:
            chunk = await sock_reader.read(8192)
            if not chunk:
                break
            client_writer.write(chunk)
            await client_writer.drain()

    async def _send_error_response(
        self,
        writer: asyncio.StreamWriter,
        status_code: int,
        message: str,
    ) -> None:
        """Send an HTTP error response to the client."""
        status_messages = {
            500: "Internal Server Error",
            502: "Bad Gateway",
            503: "Service Unavailable",
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
