"""OTel socket ingress and detached delivery for the sandboxed agent runtime.

Each job gets a per-turn 0600 Unix socket receiver (same pattern as
``llm_proxy.py``) that authenticates and validates OTLP/HTTP POSTs from the
sandboxed Claude runtime, then hands each one to a process-wide delivery pool;
decrypted exporter headers are injected pool-side and never leave the host.
Store-and-forward rather than pass-through because the most valuable batch is
the tail flush of session-total metrics at turn end, and the producer is
ephemeral — delivery must outlive the turn that admitted it.

Accepted losses: admissions past the per-turn or global budget are shed with a
503 to the exporter, and in-flight deliveries are lost on process shutdown.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
import orjson
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from pydantic import SecretStr

from tracecat.agent.tokens import AgentOtelTokenClaims, verify_agent_otel_token
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.logger import logger

OTEL_SOCKET_NAME = "otel.sock"

SignalPath = Literal["/v1/metrics", "/v1/logs", "/v1/traces"]

# frozenset[SignalPath] so membership checks narrow parsed str paths.
_SIGNAL_PATHS: frozenset[SignalPath] = frozenset(
    {"/v1/metrics", "/v1/logs", "/v1/traces"}
)

_SIGNAL_ENDPOINT_KEYS: dict[SignalPath, str] = {
    "/v1/metrics": "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "/v1/logs": "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    "/v1/traces": "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
}

_USER_AGENT = "tracecat-agent-otel-relay/1.0"

_SUPPORTED_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/x-protobuf",
    }
)

# Per the OTLP spec, 500 is terminal: retrying it can duplicate a batch the
# collector already accepted.
_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})

# Statuses whose Retry-After the OTLP spec says exporters must honor.
_RETRY_AFTER_STATUS_CODES = frozenset({429, 503})

# Telemetry payloads are bounded — anything larger than this almost certainly
# indicates a misconfiguration rather than legitimate OTel data.
MAX_BODY_SIZE = 16 * 1024 * 1024

# Delivery tuning, hardcoded pending benchmarks; tests monkeypatch these.
_TIMEOUT_SECONDS = 10.0
_MAX_PENDING_ITEMS = 1024
_MAX_PENDING_BYTES = 64 * 1024 * 1024
_MAX_CONCURRENT_REQUESTS = 8
_MAX_DELIVERY_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 0.5
# Cap on a collector-supplied Retry-After so it cannot pin a pool slot.
_MAX_RETRY_AFTER_SECONDS = 30.0
# Bound how long an admitted connection may stall the receiver mid-request.
_HEAD_READ_TIMEOUT_SECONDS = 10.0
_BODY_READ_TIMEOUT_SECONDS = 30.0

# Fairness cap so one turn cannot consume the whole global budget.
_MAX_PENDING_ITEMS_PER_TURN = 256

# Concurrent ingress connections per receiver, hardcoded pending benchmarks.
_MAX_CONNECTIONS_PER_TURN = 32

# Bound on the request line plus header block, read before any body byte.
_MAX_HEADER_SECTION_SIZE = 8 * 1024


@dataclass(frozen=True, slots=True)
class _ReceiverRequestHead:
    """Request line and the four headers this receiver retains.

    Parsed before any body byte is read so method, path, and token can be
    checked before the sandbox is allowed to send a payload.
    """

    method: str
    path: str
    content_type: str | None
    authorization: str | None
    content_length: int | None


@dataclass(frozen=True, slots=True)
class _OtelDelivery:
    """A fully resolved OTLP post, safe to run after its receiver is gone.

    Built at admission while the receiver's collector config and decrypted
    headers are live, then handed to the module-level pool.
    """

    collector_url: str
    content_type: str
    body: bytes
    headers: dict[str, str]
    # Non-sensitive discriminators for delivery logging; never the payload
    # contents and never the collector URL, whose path may carry a credential.
    signal_path: SignalPath
    workspace_id: WorkspaceID
    organization_id: OrganizationID
    session_id: UUID


@dataclass(frozen=True, slots=True)
class PlatformTraceParent:
    """Trusted platform parent used to join native agent spans to one trace."""

    trace_id: bytes
    span_id: bytes
    trace_flags: int
    resource_attributes: Mapping[str, str]


class _MalformedRequestError(Exception):
    """The sandbox request is not valid HTTP for this receiver."""


class _RequestTooLargeError(Exception):
    """The sandbox request header section exceeds the receiver's limit."""


# In-flight delivery tasks mapped to the byte budget each one holds, so a task
# stranded on a closed loop can be refunded without running its finally.
_delivery_tasks: dict[asyncio.Task[None], int] = {}

# Byte budget across reserved-but-unread bodies and in-flight deliveries. Added
# exactly once at reservation, removed exactly once by either the receiver's
# refund or the task entry being popped.
_pending_bytes = 0

# asyncio primitives bind to their loop; keyed per loop and swept alongside the
# closed-loop task sweep in _spawn_delivery.
_post_semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _get_post_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if (sem := _post_semaphores.get(loop)) is None:
        sem = _post_semaphores[loop] = asyncio.Semaphore(_MAX_CONCURRENT_REQUESTS)
    return sem


def _client_factory() -> httpx.AsyncClient:
    """Outbound client for one delivery; tests swap in a mock transport."""
    return httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT_SECONDS))


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


def _upsert_resource_attributes(
    resource: Resource,
    attributes: Mapping[str, str],
) -> None:
    """Attach trusted correlation fields, overriding sandbox-supplied values."""
    existing = {attribute.key: attribute for attribute in resource.attributes}
    for key, value in attributes.items():
        if (attribute := existing.get(key)) is not None:
            attribute.value.CopyFrom(AnyValue(string_value=value))
        else:
            resource.attributes.append(
                KeyValue(key=key, value=AnyValue(string_value=value))
            )


def reparent_platform_trace_body(
    body: bytes,
    *,
    content_type: str,
    parent: PlatformTraceParent | None,
) -> bytes:
    """Join native OTLP/protobuf spans beneath the active platform span.

    Claude's sandbox exporter is forced to HTTP/protobuf. JSON remains
    untouched for compatibility with any legacy producer. A malformed payload
    is also forwarded unchanged so best-effort telemetry cannot block a turn.
    """
    if parent is None or content_type != "application/x-protobuf" or not body:
        return body

    request = ExportTraceServiceRequest()
    try:
        request.ParseFromString(body)
    except DecodeError:
        logger.warning(
            "Could not join malformed native agent trace payload",
            error_type=DecodeError.__name__,
        )
        return body

    for resource_spans in request.resource_spans:
        _upsert_resource_attributes(
            resource_spans.resource,
            parent.resource_attributes,
        )
        for scope_spans in resource_spans.scope_spans:
            for span in scope_spans.spans:
                span.trace_id = parent.trace_id
                if not span.parent_span_id:
                    span.parent_span_id = parent.span_id
                span.flags = (span.flags & ~1) | (parent.trace_flags & 1)
    return request.SerializeToString()


def _sweep_closed_loops() -> None:
    """Evict tasks and semaphores stranded on closed (e.g. per-test) loops.

    A stranded task never runs its finally, so the sweep refunds its bytes.
    """
    for stranded in [t for t in _delivery_tasks if t.get_loop().is_closed()]:
        _release_delivery_slot(stranded)
    for closed_loop in [loop for loop in _post_semaphores if loop.is_closed()]:
        del _post_semaphores[closed_loop]


def _reserve_pending_bytes(size: int) -> bool:
    """Claim byte budget before the bytes exist, so a declared Content-Length
    is charged to the pool before the receiver buffers it."""
    global _pending_bytes

    _sweep_closed_loops()
    if _pending_bytes + size > _MAX_PENDING_BYTES:
        return False
    _pending_bytes += size
    return True


def _refund_pending_bytes(size: int) -> None:
    """Return a reservation whose delivery never reached the pool."""
    global _pending_bytes

    _pending_bytes -= size


def _spawn_delivery(
    delivery: _OtelDelivery, reserved_bytes: int
) -> asyncio.Task[None] | None:
    """Admit one pre-reserved delivery; ``None`` when the item cap is full.

    The caller already holds ``reserved_bytes`` of budget; on success that
    reservation transfers to the task, on ``None`` the caller refunds it.
    """
    _sweep_closed_loops()
    if len(_delivery_tasks) >= _MAX_PENDING_ITEMS:
        return None

    task = asyncio.get_running_loop().create_task(_deliver(delivery))
    _delivery_tasks[task] = reserved_bytes
    task.add_done_callback(_release_delivery_slot)
    return task


def _release_delivery_slot(task: asyncio.Task[None]) -> None:
    """Refund the task's byte budget. Popping here makes the refund exactly
    once, whether it lands from the done callback or the closed-loop sweep."""
    global _pending_bytes

    _pending_bytes -= _delivery_tasks.pop(task, 0)


def _parse_retry_after(status_code: int, header_value: str | None) -> float | None:
    """Delay a 429/503 asked for, capped. Only the integer-seconds form is
    honored; an http-date or malformed value falls back to normal backoff."""
    if status_code not in _RETRY_AFTER_STATUS_CODES or header_value is None:
        return None
    try:
        seconds = int(header_value.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(float(seconds), _MAX_RETRY_AFTER_SECONDS)


async def _deliver(delivery: _OtelDelivery) -> None:
    """Post one admitted item with bounded concurrency and bounded retries.

    Manual ``random``-jittered backoff rather than tenacity: the per-attempt
    delay depends on the response's ``Retry-After``, which tenacity's wait
    strategies cannot observe (same pattern as storage/utils.py and
    integrations/service.py).

    The byte budget is refunded by the task's done callback, not here, so a
    task stranded on a closed loop is still refunded by the sweep.
    """
    started = time.perf_counter()
    outbound_headers = {
        "content-type": delivery.content_type,
        "user-agent": _USER_AGENT,
        **delivery.headers,
    }
    status_code: int | None = None
    error_type: str | None = None
    attempts = 0
    # One slot spans all attempts; the socket itself is only open per post.
    async with _get_post_semaphore():
        for attempt in range(1, _MAX_DELIVERY_ATTEMPTS + 1):
            attempts = attempt
            status_code = None
            error_type = None
            retryable = False
            retry_after: float | None = None
            try:
                async with _client_factory() as client:
                    # Streamed so a hostile collector cannot make the host
                    # buffer its response; only status and headers are read.
                    async with client.stream(
                        "POST",
                        delivery.collector_url,
                        headers=outbound_headers,
                        content=delivery.body,
                    ) as response:
                        status_code = response.status_code
                        retry_after = _parse_retry_after(
                            status_code, response.headers.get("retry-after")
                        )
            except httpx.TransportError as exc:
                error_type = type(exc).__name__
                retryable = True
            except Exception as exc:
                error_type = type(exc).__name__
                break
            else:
                if 200 <= status_code < 300:
                    # Confirms the collector actually accepted the batch,
                    # which the 202 written at admission cannot. No payload
                    # contents and no collector URL on this line.
                    logger.info(
                        "Delivered agent OTel batch",
                        status_code=status_code,
                        signal_path=delivery.signal_path,
                        workspace_id=delivery.workspace_id,
                        organization_id=delivery.organization_id,
                        session_id=delivery.session_id,
                        attempts=attempts,
                        retried=attempts > 1,
                        duration_ms=round((time.perf_counter() - started) * 1000),
                    )
                    return
                retryable = status_code in _RETRYABLE_STATUS_CODES

            if not retryable or attempt == _MAX_DELIVERY_ATTEMPTS:
                break
            # The spec requires jitter so retrying exporters do not synchronize
            # into a thundering herd on the collector.
            delay = _RETRY_BASE_DELAY_SECONDS * (2.0 ** (attempt - 1))
            delay *= random.uniform(1.0, 1.5)
            await asyncio.sleep(retry_after if retry_after is not None else delay)

    # No exception text or URL on this path: the collector endpoint is
    # tenant-configured and may carry a credential in its path.
    logger.warning(
        "Failed to deliver agent OTel batch",
        error_type=error_type,
        status_code=status_code,
        signal_path=delivery.signal_path,
        workspace_id=delivery.workspace_id,
        organization_id=delivery.organization_id,
        session_id=delivery.session_id,
        attempts=attempts,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )


class OtelSocketReceiver:
    """Per-turn OTLP/HTTP ingress that admits into the process-wide pool."""

    def __init__(
        self,
        *,
        socket_path: Path,
        collector_env: Mapping[str, str],
        headers: Mapping[str, SecretStr],
        expected_workspace_id: WorkspaceID,
        expected_organization_id: OrganizationID,
        expected_session_id: UUID,
        platform_trace_parent: PlatformTraceParent | None = None,
    ) -> None:
        self.socket_path = socket_path
        self._collector_env = dict(collector_env)
        self._headers = {
            key: value.get_secret_value() for key, value in headers.items()
        }
        self._expected_workspace_id = expected_workspace_id
        self._expected_organization_id = expected_organization_id
        self._expected_session_id = expected_session_id
        self._platform_trace_parent = platform_trace_parent
        self._server: asyncio.Server | None = None
        self._connection_tasks: set[asyncio.Task[None]] = set()
        self._pending_items = 0
        self._accepting = False
        self._admitted: Counter[str] = Counter()
        self._rejected: Counter[str] = Counter()

    async def start(self) -> None:
        """Bind the Unix socket and start accepting sandbox connections."""
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()

        self._accepting = True
        self._server = await asyncio.start_unix_server(
            self._accept_connection,
            path=str(self.socket_path),
        )
        os.chmod(self.socket_path, 0o600)
        logger.info("OTel socket receiver started")

    async def stop(self) -> None:
        """Close ingress and release socket resources.

        Admitted deliveries are owned by the module-level pool and deliberately
        outlive this receiver; nothing here waits on or cancels them.
        """
        self._accepting = False
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        await self._cancel_connection_tasks()

        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass
        logger.info(
            "OTel socket receiver stopped",
            admitted=dict(self._admitted),
            rejected=dict(self._rejected),
        )

    def _accept_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Retain each ingress handler so receiver shutdown can cancel it."""
        if len(self._connection_tasks) >= _MAX_CONNECTIONS_PER_TURN:
            self._rejected["connections"] += 1
            task = asyncio.create_task(self._refuse_connection(writer))
        else:
            task = asyncio.create_task(self._handle_connection(reader, writer))
        self._connection_tasks.add(task)
        task.add_done_callback(self._connection_tasks.discard)

    async def _refuse_connection(self, writer: asyncio.StreamWriter) -> None:
        """Shed one connection over the cap without reading its request."""
        try:
            await self._write_response(
                writer, status_code=503, reason="Service Unavailable"
            )
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _cancel_connection_tasks(self) -> None:
        """Cancel request parsing and response writers after ingress closes."""
        tasks = tuple(self._connection_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Parse one inbound HTTP request, admit it, write the response.

        No per-request logs: telemetry bodies and headers are tenant data and
        must not leak via byte counts, header names, or URL components. The
        receiver aggregates admissions/rejections and emits a single summary
        on stop().
        """
        # Reservation held across the body read; refunded in finally unless a
        # spawned task took ownership of it.
        reserved = 0
        try:
            try:
                async with asyncio.timeout(_HEAD_READ_TIMEOUT_SECONDS):
                    head = await self._read_request_head(reader)
            except TimeoutError:
                self._rejected["timeout"] += 1
                await self._write_response(
                    writer, status_code=408, reason="Request Timeout"
                )
                return
            except _RequestTooLargeError:
                self._rejected["headers_too_large"] += 1
                await self._write_response(
                    writer, status_code=431, reason="Request Header Fields Too Large"
                )
                return
            except _MalformedRequestError:
                self._rejected["malformed_request"] += 1
                await self._write_response(
                    writer, status_code=400, reason="Bad Request"
                )
                return

            if head is None:
                return
            if head.method != "POST":
                self._rejected["method"] += 1
                await self._write_response(
                    writer, status_code=405, reason="Method Not Allowed"
                )
                return

            normalized_path = head.path.split("?", 1)[0]
            if normalized_path not in _SIGNAL_PATHS:
                self._rejected["path"] += 1
                await self._write_response(writer, status_code=404, reason="Not Found")
                return

            # Auth precedes every body byte: an unauthenticated sandbox must
            # not be able to make the host buffer a payload.
            claims = self._verify_inbound_auth(head.authorization)
            if claims is None:
                self._rejected["identity"] += 1
                await self._write_response(
                    writer, status_code=401, reason="Unauthorized"
                )
                return
            if not self._claims_match_expected_context(claims):
                self._rejected["context"] += 1
                await self._write_response(writer, status_code=403, reason="Forbidden")
                return

            if head.content_length is None:
                self._rejected["length_required"] += 1
                await self._write_response(
                    writer, status_code=411, reason="Length Required"
                )
                return
            if head.content_length > MAX_BODY_SIZE:
                self._rejected["body_too_large"] += 1
                await self._write_response(
                    writer, status_code=413, reason="Payload Too Large"
                )
                return

            content_type = self._normalize_content_type(head.content_type)
            if content_type is None:
                self._rejected["content_type"] += 1
                await self._write_response(
                    writer, status_code=415, reason="Unsupported Media Type"
                )
                return

            # Charged before the body is read, so an authed sandbox cannot
            # buffer payloads the pool has no budget for.
            if not _reserve_pending_bytes(head.content_length):
                self._rejected["pool_capacity"] += 1
                await self._write_response(
                    writer, status_code=503, reason="Service Unavailable"
                )
                return
            reserved = head.content_length

            # Resolved at admission so the delivery item stays self-contained.
            collector_url = resolve_collector_url(self._collector_env, normalized_path)
            if collector_url is None or not self._accepting:
                self._rejected["collector"] += 1
                await self._write_response(
                    writer, status_code=503, reason="No Collector"
                )
                return

            body = b""
            if head.content_length > 0:
                try:
                    async with asyncio.timeout(_BODY_READ_TIMEOUT_SECONDS):
                        body = await reader.readexactly(head.content_length)
                except TimeoutError:
                    self._rejected["timeout"] += 1
                    await self._write_response(
                        writer, status_code=408, reason="Request Timeout"
                    )
                    return

            if normalized_path == "/v1/traces":
                rewritten_body = reparent_platform_trace_body(
                    body,
                    content_type=content_type,
                    parent=self._platform_trace_parent,
                )
                size_delta = len(rewritten_body) - len(body)
                if size_delta > 0:
                    if not _reserve_pending_bytes(size_delta):
                        self._rejected["pool_capacity"] += 1
                        await self._write_response(
                            writer, status_code=503, reason="Service Unavailable"
                        )
                        return
                    reserved += size_delta
                elif size_delta < 0:
                    _refund_pending_bytes(-size_delta)
                    reserved += size_delta
                body = rewritten_body

            delivery = _OtelDelivery(
                collector_url=collector_url,
                content_type=content_type,
                body=body,
                headers=dict(self._headers),
                signal_path=normalized_path,
                workspace_id=self._expected_workspace_id,
                organization_id=self._expected_organization_id,
                session_id=self._expected_session_id,
            )
            if not self._admit(delivery, reserved):
                await self._write_response(
                    writer, status_code=503, reason="Service Unavailable"
                )
                return
            # The task owns the reservation now; the finally must not refund.
            reserved = 0

            await self._write_response(
                writer,
                status_code=202,
                reason="Accepted",
            )
        except (asyncio.IncompleteReadError, ConnectionError):
            # Client disconnect mid-request is normal during sandbox teardown.
            return
        except Exception as exc:
            # Unexpected failures still need a log so they don't disappear; we
            # only omit per-request payload metadata, not the failure itself.
            logger.exception("OTel receiver error", error_type=type(exc).__name__)
        finally:
            if reserved:
                _refund_pending_bytes(reserved)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _normalize_content_type(content_type: str | None) -> str | None:
        """Return one canonical OTLP media type or reject the sandbox value."""
        media_type = (content_type or "").partition(";")[0].strip().lower()
        if media_type not in _SUPPORTED_CONTENT_TYPES:
            return None
        return media_type

    def _admit(self, delivery: _OtelDelivery, reserved_bytes: int) -> bool:
        """Check the per-turn fairness cap, then hand off to the global pool.

        The caller's byte reservation transfers to the spawned task only when
        this returns True; otherwise the caller still owns and refunds it.
        """
        if self._pending_items >= _MAX_PENDING_ITEMS_PER_TURN:
            self._shed("turn_capacity", delivery)
            return False
        task = _spawn_delivery(delivery, reserved_bytes)
        if task is None:
            self._shed("pool_capacity", delivery)
            return False

        self._pending_items += 1
        self._admitted[delivery.signal_path] += 1
        # Frees turn budget even after stop(); the pool owns the task itself.
        task.add_done_callback(self._release_turn_slot)
        return True

    def _release_turn_slot(self, task: asyncio.Task[None]) -> None:
        self._pending_items -= 1

    def _shed(self, reason: str, delivery: _OtelDelivery) -> None:
        """Log and count one shed admission. No URL, no payload."""
        self._rejected[reason] += 1
        logger.warning(
            "Shed agent OTel batch; delivery capacity reached",
            reason=reason,
            signal_path=delivery.signal_path,
            workspace_id=delivery.workspace_id,
            organization_id=delivery.organization_id,
            session_id=delivery.session_id,
        )

    def _verify_inbound_auth(
        self, auth_header: str | None
    ) -> AgentOtelTokenClaims | None:
        """Verify the receiver's internal bearer token."""
        scheme, _, token = (auth_header or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        try:
            return verify_agent_otel_token(token.strip())
        except ValueError:
            return None

    def _claims_match_expected_context(self, claims: AgentOtelTokenClaims) -> bool:
        """Check token identity matches the receiver instance's context."""
        return (
            claims.workspace_id == self._expected_workspace_id
            and claims.organization_id == self._expected_organization_id
            and claims.session_id == self._expected_session_id
        )

    @staticmethod
    async def _read_request_head(
        reader: asyncio.StreamReader,
    ) -> _ReceiverRequestHead | None:
        """Parse the HTTP request line and headers, and no body byte.

        Deliberately retains only four pieces of state: method, path,
        content-type, and authorization. Every other inbound header is
        discarded at parse time so it cannot reach the outbound request
        built at admission. Do not accumulate an inbound header dict here;
        the receiver's contract is that the sandbox cannot influence
        outbound headers beyond echoing content-type.
        """
        consumed = 0

        async def _read_head_line() -> bytes:
            nonlocal consumed
            try:
                line = await reader.readuntil(b"\n")
            except asyncio.LimitOverrunError as exc:
                raise _RequestTooLargeError from exc
            consumed += len(line)
            if consumed > _MAX_HEADER_SECTION_SIZE:
                raise _RequestTooLargeError
            return line

        request_line = await reader.readline()
        if not request_line:
            return None
        consumed += len(request_line)
        if consumed > _MAX_HEADER_SECTION_SIZE:
            raise _RequestTooLargeError

        try:
            parts = request_line.decode("ascii").strip().split(" ", 2)
        except UnicodeDecodeError as exc:
            raise _MalformedRequestError from exc
        if len(parts) < 2:
            raise _MalformedRequestError
        method, path = parts[0], parts[1]

        content_length: int | None = None
        content_type: str | None = None
        auth_header: str | None = None
        while True:
            line = await _read_head_line()
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
                except ValueError as exc:
                    raise _MalformedRequestError from exc
                if content_length < 0:
                    raise _MalformedRequestError
            elif key_lower == "content-type":
                content_type = value
            elif key_lower == "authorization":
                auth_header = value

        return _ReceiverRequestHead(
            method=method,
            path=path,
            content_type=content_type,
            authorization=auth_header,
            content_length=content_length,
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
