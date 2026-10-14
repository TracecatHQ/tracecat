"""Bounded, fair delivery of agent OTLP exports to their collectors.

One pool serves every turn on an agent executor. Each share (an organization,
or ``"platform"`` for the redacted platform copies) may hold a quarter of the
pool's items and bytes and has its own delivery slots, so a collector that
stalls holds only its own share. Deliveries outlive the turn that admitted
them; shutdown drains them for a bounded time and cancels the rest.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Literal
from uuid import UUID

import httpx

from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.logger import logger

SignalPath = Literal["/v1/metrics", "/v1/logs", "/v1/traces"]

type ShareKey = OrganizationID | Literal["platform"]

_USER_AGENT = "tracecat-agent-otel-relay/1.0"

# Per the OTLP spec, 500 is terminal: retrying it can duplicate a batch the
# collector already accepted.
_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})

# Statuses whose Retry-After the OTLP spec says exporters must honor.
_RETRY_AFTER_STATUS_CODES = frozenset({429, 503})

_RETRY_BASE_DELAY_SECONDS = 0.5

# An OTLP export response is a small Export*ServiceResponse. Reading one this
# size to its end returns the connection to the pool for the next delivery.
_MAX_DISCARDED_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class DeliveryLimits:
    """Budgets and retry policy for one delivery pool."""

    max_items: int = 1024
    """Pooled deliveries across every share."""
    max_bytes: int = 64 * 1024 * 1024
    """Bytes of bodies being read and of pooled deliveries, across every share."""
    share_items: int = 256
    """Pooled deliveries one share may hold."""
    share_bytes: int = 16 * 1024 * 1024
    """Bytes one share may hold, counting bodies still being read."""
    posts: int = 32
    """Posts in flight across every share; each is held for one attempt."""
    share_posts: int = 8
    """Deliveries in progress per share; each is held through retries and backoff."""
    attempts: int = 3
    attempt_seconds: float = 10.0
    """Wall-clock deadline per attempt. HTTPX's timeouts bound only inactivity,
    which a collector that keeps sending informational responses never hits."""
    max_retry_after_seconds: float = 30.0
    """Cap on a collector's Retry-After, so it cannot pin a share's slot."""
    drain_seconds: float = 45.0
    """How long shutdown waits for pending deliveries before cancelling them."""


@dataclass(frozen=True, slots=True)
class OtelDelivery:
    """One resolved OTLP post, safe to run after the turn that admitted it."""

    share: ShareKey
    signal: SignalPath
    # The collector path may carry a credential.
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    content_type: str
    body: bytes = field(repr=False)
    organization_id: OrganizationID
    workspace_id: WorkspaceID
    session_id: UUID


@dataclass(slots=True)
class _Share:
    """A share's part of the pool: its pooled deliveries, held bytes and slots."""

    posts: asyncio.Semaphore
    items: int = 0
    reserved: int = 0


class Reservation:
    """Bytes charged to one share before the body they stand for is pooled.

    A reservation grows while a body is read, hands its bytes to the copies
    ``OtelDeliveryPool.admit`` pools on its share, and refunds the rest on
    ``close``.
    """

    __slots__ = ("_pool", "share", "size")

    share: ShareKey
    size: int
    """Bytes held that ``admit`` has not handed to a delivery."""

    def __init__(self, pool: OtelDeliveryPool, share: ShareKey, size: int) -> None:
        self._pool = pool
        self.share = share
        self.size = size

    def grow(self, size: int) -> bool:
        """Charge ``size`` more bytes, or refuse without charging any.

        Args:
            size: Bytes to add to this reservation.

        Returns:
            Whether the pool and this share had room for them.
        """
        if not self._pool._try_hold(self.share, size):
            return False
        self.size += size
        return True

    def close(self) -> None:
        """Refund whatever ``admit`` did not take. Safe to call more than once."""
        size, self.size = self.size, 0
        if size:
            self._pool._refund(self.share, size)


def collector_client(
    *,
    trust_env: bool,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build the outbound client every share's deliveries go through.

    It follows no redirect, and its jar refuses every cookie, so a collector's
    Set-Cookie never reaches a later delivery, for its own org or another.

    Args:
        trust_env: Whether to take proxy and TLS settings from the environment.
        transport: Transport to use instead of HTTPX's default.

    Returns:
        A client the caller closes after draining the pool that uses it.
    """
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(10.0),
        follow_redirects=False,
        trust_env=trust_env,
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
    )


def _retry_after_seconds(
    status_code: int, header_value: str | None, cap: float
) -> float | None:
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
    # Clamped before conversion: float() overflows on a huge integer.
    return float(min(seconds, cap))


async def _discard_response_body(response: httpx.Response) -> None:
    """Read and drop a small response body so its connection can be reused.

    Each chunk is dropped as it arrives, so a hostile collector cannot make
    the host buffer its body. A body past the cap is left unread, and closing
    the response then closes its connection instead of pooling it.
    """
    # A transport that returns the body already read (httpx.MockTransport)
    # leaves nothing to discard.
    if response.is_stream_consumed:
        return
    discarded = 0
    async for chunk in response.aiter_raw():
        discarded += len(chunk)
        if discarded > _MAX_DISCARDED_RESPONSE_BYTES:
            return


class OtelDeliveryPool:
    """Admit OTLP copies into bounded per-share budgets and deliver them."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        limits: DeliveryLimits | None = None,
    ) -> None:
        self.client = client
        self.limits = limits or DeliveryLimits()
        self._posts = asyncio.Semaphore(self.limits.posts)
        # Shares holding reserved bytes or pooled deliveries.
        self._shares: dict[ShareKey, _Share] = {}
        self._tasks: dict[asyncio.Task[None], OtelDelivery] = {}
        # Bytes of bodies being read and of pooled deliveries, across shares.
        self._reserved = 0

    def reserve(self, share: ShareKey, size: int = 0) -> Reservation | None:
        """Charge a body's bytes to a share before the body is buffered.

        Args:
            share: Share the body is read for.
            size: Bytes to charge now; ``Reservation.grow`` adds more.

        Returns:
            The reservation, or None when the pool or share has no room.
        """
        if not self._try_hold(share, size):
            return None
        return Reservation(self, share, size)

    def admit(
        self,
        reservation: Reservation,
        deliveries: Sequence[OtelDelivery],
    ) -> bool:
        """Pool every copy of one export, or none of them.

        Each copy is charged to its own share. The reservation's bytes pay for
        copies on its share first; whatever the copies need beyond that must
        fit now. Refusal charges nothing, and ``reservation.close()`` still
        refunds what the reservation holds.

        Args:
            reservation: Bytes held for the body the copies were built from.
            deliveries: The copies to pool.

        Returns:
            Whether every copy was pooled.
        """
        items: Counter[ShareKey] = Counter()
        extra: Counter[ShareKey] = Counter()
        for delivery in deliveries:
            items[delivery.share] += 1
            extra[delivery.share] += len(delivery.body)
        taken = min(extra[reservation.share], reservation.size)
        if taken:
            extra[reservation.share] -= taken

        if not self._fits(items, extra):
            for delivery in deliveries:
                logger.warning(
                    "Shed agent OTel batch; delivery capacity reached",
                    signal_path=delivery.signal,
                    workspace_id=delivery.workspace_id,
                    organization_id=delivery.organization_id,
                    session_id=delivery.session_id,
                )
            return False

        reservation.size -= taken
        for key, size in extra.items():
            self._hold(key, size)
        for delivery in deliveries:
            share = self._share(delivery.share)
            share.items += 1
            task = asyncio.get_running_loop().create_task(
                self._deliver(delivery, share.posts)
            )
            self._tasks[task] = delivery
            task.add_done_callback(self._release)
        return True

    async def drain(self) -> None:
        """Give pending deliveries ``drain_seconds`` to finish, then cancel the rest."""
        if not self._tasks:
            return
        done, pending = await asyncio.wait(
            set(self._tasks), timeout=self.limits.drain_seconds
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        logger.info(
            "Drained agent OTel deliveries",
            finished=len(done),
            cancelled=len(pending),
        )

    def _fits(self, items: Counter[ShareKey], extra: Counter[ShareKey]) -> bool:
        """Whether new copies fit the pool's and each share's budgets."""
        limits = self.limits
        if len(self._tasks) + items.total() > limits.max_items:
            return False
        if self._reserved + extra.total() > limits.max_bytes:
            return False
        for key in items:
            share = self._shares.get(key)
            held_items = share.items if share is not None else 0
            held_bytes = share.reserved if share is not None else 0
            if held_items + items[key] > limits.share_items:
                return False
            if held_bytes + extra[key] > limits.share_bytes:
                return False
        return True

    def _share(self, key: ShareKey) -> _Share:
        """The share's state, created on first use."""
        if (share := self._shares.get(key)) is None:
            share = self._shares[key] = _Share(
                posts=asyncio.Semaphore(self.limits.share_posts)
            )
        return share

    def _try_hold(self, key: ShareKey, size: int) -> bool:
        """Charge bytes to a share if both budgets have room."""
        share = self._shares.get(key)
        held = share.reserved if share is not None else 0
        if self._reserved + size > self.limits.max_bytes:
            return False
        if held + size > self.limits.share_bytes:
            return False
        self._hold(key, size)
        return True

    def _hold(self, key: ShareKey, size: int) -> None:
        if size:
            self._share(key).reserved += size
            self._reserved += size

    def _refund(self, key: ShareKey, size: int) -> None:
        """Return bytes, and drop the share once it holds nothing."""
        self._reserved -= size
        share = self._shares[key]
        share.reserved -= size
        if not share.items and not share.reserved:
            del self._shares[key]

    def _release(self, task: asyncio.Task[None]) -> None:
        delivery = self._tasks.pop(task)
        self._shares[delivery.share].items -= 1
        self._refund(delivery.share, len(delivery.body))

    async def _deliver(
        self, delivery: OtelDelivery, share_posts: asyncio.Semaphore
    ) -> None:
        """Post one delivery with bounded attempts and jittered backoff.

        Manual ``random``-jittered backoff rather than tenacity: the delay
        depends on the response's ``Retry-After``, which tenacity's wait
        strategies cannot observe.
        """
        limits = self.limits
        started = time.perf_counter()
        headers = {
            "content-type": delivery.content_type,
            "user-agent": _USER_AGENT,
            **delivery.headers,
        }
        status_code: int | None = None
        error_type: str | None = None
        attempt = 0
        # The share's slot spans every attempt and backoff; a shared slot spans
        # one attempt, so a slow or throttling collector holds shared slots
        # only until its attempt deadline.
        async with share_posts:
            for attempt in range(1, limits.attempts + 1):
                status_code = None
                error_type = None
                retry_after: float | None = None
                try:
                    async with self._posts, asyncio.timeout(limits.attempt_seconds):
                        # Streamed so a hostile collector cannot make the host
                        # buffer its response.
                        async with self.client.stream(
                            "POST",
                            delivery.url,
                            headers=headers,
                            content=delivery.body,
                        ) as response:
                            status_code = response.status_code
                            retry_after = _retry_after_seconds(
                                response.status_code,
                                response.headers.get("retry-after"),
                                limits.max_retry_after_seconds,
                            )
                            await _discard_response_body(response)
                except (httpx.TransportError, TimeoutError) as exc:
                    error_type = type(exc).__name__
                except Exception as exc:
                    error_type = type(exc).__name__
                    break

                if status_code is not None and 200 <= status_code < 300:
                    # Confirms the collector accepted the batch, which the 202
                    # written at admission cannot.
                    # No payload contents and no collector URL on this line.
                    logger.info(
                        "Delivered agent OTel batch",
                        status_code=status_code,
                        signal_path=delivery.signal,
                        workspace_id=delivery.workspace_id,
                        organization_id=delivery.organization_id,
                        session_id=delivery.session_id,
                        attempts=attempt,
                        retried=attempt > 1,
                        duration_ms=round((time.perf_counter() - started) * 1000),
                    )
                    return
                # A transport error or a missed deadline before the status
                # arrived is retried. One while discarding the body keeps the
                # status, so a batch the collector accepted is not resent.
                retryable = (
                    status_code is None or status_code in _RETRYABLE_STATUS_CODES
                )
                if not retryable or attempt == limits.attempts:
                    break
                # The spec requires jitter so retrying exporters do not
                # synchronize into a thundering herd on the collector.
                delay = _RETRY_BASE_DELAY_SECONDS * (2.0 ** (attempt - 1))
                delay *= random.uniform(1.0, 1.5)
                await asyncio.sleep(retry_after if retry_after is not None else delay)

        # No exception text or URL: the collector endpoint is tenant-configured
        # and may carry a credential in its path.
        logger.warning(
            "Failed to deliver agent OTel batch",
            error_type=error_type,
            status_code=status_code,
            signal_path=delivery.signal,
            workspace_id=delivery.workspace_id,
            organization_id=delivery.organization_id,
            session_id=delivery.session_id,
            attempts=attempt,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
