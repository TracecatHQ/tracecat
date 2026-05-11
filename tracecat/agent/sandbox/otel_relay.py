"""OTel socket relay for sandboxed agent runtime.

Host-side Unix socket relay that forwards OTLP/HTTP telemetry POSTs from the
sandboxed Claude runtime to a tenant-configured collector. Decrypted exporter
headers are injected here and never leave the trusted host process.

Peer of the LLM proxy/bridge pair, intentionally separate so a fault in one
side cannot bring down the other (failure, backpressure, vulnerability,
observability, lifecycle).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx
import orjson
from pydantic import SecretStr

from tracecat.agent.tokens import AgentOtelTokenClaims, verify_agent_otel_token
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.logger import logger

OTEL_SOCKET_NAME = "otel.sock"

_SIGNAL_PATHS = frozenset({"/v1/metrics", "/v1/logs", "/v1/traces"})

_SIGNAL_ENDPOINT_KEYS = {
    "/v1/metrics": "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "/v1/logs": "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    "/v1/traces": "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
}

_USER_AGENT = "tracecat-agent-otel-relay/1.0"

# Telemetry payloads are bounded — anything larger than this almost certainly
# indicates a misconfiguration rather than legitimate OTel data.
MAX_BODY_SIZE = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _RelayRequest:
    method: str
    path: str
    content_type: str | None
    authorization: str | None
    body: bytes


def resolve_collector_url(collector_env: Mapping[str, str], path: str) -> str | None:
    """Pick the upstream collector URL for an OTLP signal path.

    Prefers the signal-specific endpoint when set; otherwise falls back to
    the generic endpoint with the path appended. Returns ``None`` when no
    endpoint is configured for the requested signal.
    """
    if path not in _SIGNAL_PATHS:
        return None
    if signal_endpoint := collector_env.get(_SIGNAL_ENDPOINT_KEYS[path]):
        return signal_endpoint.rstrip("/")
    if generic := collector_env.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return f"{generic.rstrip('/')}{path}"
    return None


class OtelSocketRelay:
    """Unix socket relay forwarding OTLP/HTTP signals to a tenant collector."""

    def __init__(
        self,
        *,
        socket_path: Path,
        collector_env: Mapping[str, str],
        headers: Mapping[str, SecretStr],
        timeout_seconds: float,
        expected_workspace_id: WorkspaceID,
        expected_organization_id: OrganizationID,
        expected_session_id: UUID,
    ) -> None:
        self.socket_path = socket_path
        self._collector_env = dict(collector_env)
        self._headers = {
            key: value.get_secret_value() for key, value in headers.items()
        }
        self._timeout_seconds = timeout_seconds
        self._expected_workspace_id = expected_workspace_id
        self._expected_organization_id = expected_organization_id
        self._expected_session_id = expected_session_id
        self._server: asyncio.Server | None = None
        self._client: httpx.AsyncClient | None = None
        self._attempted: set[str] = set()
        self._succeeded: set[str] = set()

    async def start(self) -> None:
        """Bind the Unix socket and prepare the outbound HTTP client."""
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds),
        )
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.socket_path),
        )
        os.chmod(self.socket_path, 0o600)
        logger.info("OTel socket relay started")

    async def stop(self) -> None:
        """Stop the relay and remove the Unix socket."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass
        # Per-signal endpoint sets only — no counts, no body sizes, no headers.
        logger.info(
            "OTel socket relay stopped",
            attempted=sorted(self._attempted),
            succeeded=sorted(self._succeeded),
        )

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Parse one inbound HTTP request, forward it, write the response.

        No per-request logs: telemetry bodies and headers are tenant data and
        must not leak via byte counts, header names, or URL components. The
        relay aggregates attempts/successes by signal and emits a single
        summary on stop().
        """
        try:
            request = await self._read_request(reader)
            if request is None or request.method != "POST":
                await self._write_response(
                    writer, status_code=405, reason="Method Not Allowed"
                )
                return

            normalized_path = request.path.split("?", 1)[0]
            if normalized_path not in _SIGNAL_PATHS:
                await self._write_response(writer, status_code=404, reason="Not Found")
                return

            claims = self._verify_inbound_auth(request.authorization)
            if claims is None:
                await self._write_response(
                    writer, status_code=401, reason="Unauthorized"
                )
                return
            if not self._claims_match_expected_context(claims):
                await self._write_response(writer, status_code=403, reason="Forbidden")
                return

            url = resolve_collector_url(self._collector_env, normalized_path)
            if url is None or self._client is None:
                await self._write_response(
                    writer, status_code=503, reason="No Collector"
                )
                return

            outbound_headers = {"user-agent": _USER_AGENT}
            if request.content_type:
                outbound_headers["content-type"] = request.content_type
            outbound_headers.update(self._headers)

            self._attempted.add(normalized_path)
            try:
                response = await self._client.post(
                    url,
                    headers=outbound_headers,
                    content=request.body,
                )
            except httpx.HTTPError:
                await self._write_response(
                    writer, status_code=502, reason="Bad Gateway"
                )
                return

            if response.status_code < 400:
                self._succeeded.add(normalized_path)

            await self._write_response(
                writer,
                status_code=response.status_code,
                reason=response.reason_phrase or "",
                body=response.content,
                content_type=response.headers.get("content-type"),
            )
        except (asyncio.IncompleteReadError, ConnectionError):
            # Client disconnect mid-request is normal during sandbox teardown.
            return
        except Exception as exc:
            # Unexpected failures still need a log so they don't disappear; we
            # only omit per-request payload metadata, not the failure itself.
            logger.exception("OTel relay error", error_type=type(exc).__name__)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _verify_inbound_auth(
        self, auth_header: str | None
    ) -> AgentOtelTokenClaims | None:
        """Verify the relay's internal bearer token."""
        scheme, _, token = (auth_header or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        try:
            return verify_agent_otel_token(token.strip())
        except ValueError:
            return None

    def _claims_match_expected_context(self, claims: AgentOtelTokenClaims) -> bool:
        """Check token identity matches the relay instance's execution context."""
        return (
            claims.workspace_id == self._expected_workspace_id
            and claims.organization_id == self._expected_organization_id
            and claims.session_id == self._expected_session_id
        )

    @staticmethod
    async def _read_request(
        reader: asyncio.StreamReader,
    ) -> _RelayRequest | None:
        """Parse the HTTP request line, headers, and body."""
        request_line = await reader.readline()
        if not request_line:
            return None

        try:
            parts = request_line.decode("ascii").strip().split(" ", 2)
        except UnicodeDecodeError:
            return None
        if len(parts) < 2:
            return None
        method, path = parts[0], parts[1]

        content_length = 0
        content_type: str | None = None
        auth_header: str | None = None
        while True:
            line = await reader.readline()
            if not line or line == b"\r\n":
                break
            try:
                key, _, value = line.decode("ascii").strip().partition(":")
            except UnicodeDecodeError:
                continue
            key_lower = key.strip().lower()
            value = value.strip()
            if key_lower == "content-length":
                try:
                    content_length = int(value)
                except ValueError:
                    return None
            elif key_lower == "content-type":
                content_type = value
            elif key_lower == "authorization":
                auth_header = value

        if content_length < 0 or content_length > MAX_BODY_SIZE:
            return None

        body = b""
        if content_length > 0:
            body = await reader.readexactly(content_length)
        return _RelayRequest(
            method=method,
            path=path,
            content_type=content_type,
            authorization=auth_header,
            body=body,
        )

    @staticmethod
    async def _write_response(
        writer: asyncio.StreamWriter,
        *,
        status_code: int,
        reason: str,
        body: bytes = b"",
        content_type: str | None = None,
    ) -> None:
        """Write a minimal HTTP/1.1 response with Connection: close."""
        if not body and status_code >= 400:
            body = orjson.dumps({"status_code": status_code, "reason": reason})
            content_type = content_type or "application/json"

        head_parts = [
            f"HTTP/1.1 {status_code} {reason}".rstrip(),
            f"Content-Length: {len(body)}",
            "Connection: close",
        ]
        if content_type:
            head_parts.append(f"Content-Type: {content_type}")
        head = ("\r\n".join(head_parts) + "\r\n\r\n").encode("ascii")
        try:
            writer.write(head)
            if body:
                writer.write(body)
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            return
