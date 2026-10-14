"""OTel delivery pool: per-share budgets, bounded delivery, and shutdown drain."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import httpx
import pytest

from tracecat.agent import otel_delivery
from tracecat.agent.otel_delivery import (
    DeliveryLimits,
    OtelDelivery,
    OtelDeliveryPool,
    ShareKey,
    collector_client,
)

COLLECTOR = "https://collector.example.com/v1/traces"
BODY = b"\n\x05spans"

type Collector = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry at once unless a test asks for a delay."""
    monkeypatch.setattr(otel_delivery, "_RETRY_BASE_DELAY_SECONDS", 0.0)


class _EndlessStream(httpx.AsyncByteStream):
    """Response body that never ends, counting the bytes anything pulls from it."""

    chunk = b"x" * 4096

    def __init__(self) -> None:
        self.pulled = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            self.pulled += len(self.chunk)
            yield self.chunk

    async def aclose(self) -> None:
        self.closed = True


def _delivery(
    share: ShareKey,
    organization_id: uuid.UUID,
    body: bytes = BODY,
    *,
    url: str = COLLECTOR,
) -> OtelDelivery:
    """One copy for `share`, carrying the org's own collector credential."""
    return OtelDelivery(
        share=share,
        signal="/v1/traces",
        url=url,
        headers={"authorization": f"Bearer {organization_id}"},
        content_type="application/x-protobuf",
        body=body,
        organization_id=organization_id,
        workspace_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )


def _export(
    pool: OtelDeliveryPool,
    organization_id: uuid.UUID,
    body: bytes = BODY,
    *,
    platform_body: bytes | None = None,
    url: str = COLLECTOR,
) -> bool:
    """Reserve, admit and close one export the way the receiver does."""
    reservation = pool.reserve(organization_id, len(body))
    if reservation is None:
        return False
    try:
        deliveries = [_delivery(organization_id, organization_id, body, url=url)]
        if platform_body is not None:
            deliveries.append(_delivery("platform", organization_id, platform_body))
        return pool.admit(reservation, deliveries)
    finally:
        reservation.close()


def _received(captured: list[httpx.Request]) -> Collector:
    async def collector(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200)

    return collector


def _released(release: asyncio.Event, captured: list[httpx.Request]) -> Collector:
    """A collector that answers only once `release` is set."""

    async def collector(request: httpx.Request) -> httpx.Response:
        await release.wait()
        captured.append(request)
        return httpx.Response(200)

    return collector


@asynccontextmanager
async def _pool(
    collector: Collector,
    limits: DeliveryLimits | None = None,
) -> AsyncIterator[OtelDeliveryPool]:
    async with collector_client(
        trust_env=False, transport=httpx.MockTransport(collector)
    ) as client:
        pool = OtelDeliveryPool(client, limits)
        try:
            yield pool
        finally:
            await pool.drain()


@pytest.mark.anyio
async def test_admit_charges_each_copy_to_its_own_share() -> None:
    """The tenant copy lands on the org's share and the redacted copy on the platform's."""
    release = asyncio.Event()
    captured: list[httpx.Request] = []
    organization_id = uuid.uuid4()
    async with _pool(_released(release, captured)) as pool:
        reservation = pool.reserve(organization_id, 10)
        assert reservation is not None
        admitted = pool.admit(
            reservation,
            [
                _delivery(organization_id, organization_id, b"t" * 10),
                _delivery("platform", organization_id, b"p" * 4),
            ],
        )
        reservation.close()

        assert admitted
        assert pool._shares.keys() == {organization_id, "platform"}
        assert pool._shares[organization_id].items == 1
        assert pool._shares[organization_id].reserved == 10
        assert pool._shares["platform"].items == 1
        assert pool._shares["platform"].reserved == 4
        assert pool._reserved == 14

        release.set()
        await pool.drain()
        assert pool._shares == {}
        assert pool._reserved == 0
    assert len(captured) == 2


@pytest.mark.anyio
async def test_close_refunds_what_admit_did_not_take() -> None:
    """Reserved bytes the copies did not need return to the share on close."""
    release = asyncio.Event()
    organization_id = uuid.uuid4()
    async with _pool(_released(release, [])) as pool:
        reservation = pool.reserve(organization_id, 100)
        assert reservation is not None
        assert pool.admit(
            reservation, [_delivery(organization_id, organization_id, b"t" * 60)]
        )
        assert reservation.size == 40
        assert pool._shares[organization_id].reserved == 100

        reservation.close()
        reservation.close()
        assert pool._shares[organization_id].reserved == 60
        assert pool._reserved == 60
        release.set()


@pytest.mark.anyio
async def test_admit_pools_every_copy_or_none() -> None:
    """A full platform share refuses the tenant copy too, charging no share."""
    release = asyncio.Event()
    first, second = uuid.uuid4(), uuid.uuid4()
    async with _pool(_released(release, []), DeliveryLimits(share_items=1)) as pool:
        assert _export(pool, first, platform_body=b"p")
        assert not _export(pool, second, platform_body=b"p")
        assert second not in pool._shares
        assert len(pool._tasks) == 2
        # Without a platform copy the second org still fits its own share.
        assert _export(pool, second)
        release.set()


@pytest.mark.anyio
async def test_copies_larger_than_their_reservation_must_fit_the_share() -> None:
    """Bytes a copy needs beyond its reservation are charged at admission and
    must fit the share."""
    organization_id = uuid.uuid4()
    async with _pool(_received([]), DeliveryLimits(share_bytes=10)) as pool:
        overflows = pool.reserve(organization_id, 8)
        assert overflows is not None
        assert not pool.admit(
            overflows, [_delivery(organization_id, organization_id, b"t" * 11)]
        )
        overflows.close()
        assert pool._shares == {}

        fits = pool.reserve(organization_id, 8)
        assert fits is not None
        assert pool.admit(
            fits, [_delivery(organization_id, organization_id, b"t" * 10)]
        )
        fits.close()
        assert pool._shares[organization_id].reserved == 10


@pytest.mark.anyio
async def test_pool_budget_sheds_exports_and_refunds_on_delivery() -> None:
    """Byte, per-share and pool caps refuse an export; finished deliveries free them."""
    release = asyncio.Event()
    captured: list[httpx.Request] = []
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    limits = DeliveryLimits(max_bytes=2 * len(BODY), share_items=1, max_items=2)
    async with _pool(_released(release, captured), limits) as pool:
        assert _export(pool, first)
        # The first org is at its cap; another org still fits.
        assert not _export(pool, first)
        assert _export(pool, second)
        # Two pending bodies fill both the byte budget and the pool.
        assert pool._reserved == 2 * len(BODY)
        assert not _export(pool, third)
        assert pool._reserved == 2 * len(BODY)

        release.set()
        await pool.drain()
        assert pool._reserved == 0
        assert pool._shares == {}
        assert _export(pool, third)
    assert len(captured) == 3


@pytest.mark.anyio
async def test_reserve_refuses_a_body_the_pool_cannot_hold() -> None:
    """A body that would overflow the byte budget is refused before it is buffered."""
    async with _pool(_received([]), DeliveryLimits(max_bytes=len(BODY) - 1)) as pool:
        assert pool.reserve(uuid.uuid4(), len(BODY)) is None
        assert pool._reserved == 0
        assert pool._shares == {}


@pytest.mark.anyio
async def test_refused_growth_charges_nothing() -> None:
    """A reservation that cannot grow keeps exactly what it already held."""
    organization_id = uuid.uuid4()
    async with _pool(_received([]), DeliveryLimits(share_bytes=1024)) as pool:
        reservation = pool.reserve(organization_id)
        assert reservation is not None
        assert reservation.grow(1000)
        assert not reservation.grow(100)
        assert reservation.size == 1000
        assert pool._shares[organization_id].reserved == 1000
        reservation.close()
        assert pool._shares == {}
        assert pool._reserved == 0


@pytest.mark.anyio
async def test_share_byte_budget_counts_bodies_still_being_read() -> None:
    """Bytes of a body still arriving count against its share, so a concurrent
    export from that org that would overflow is refused; another org's is not."""
    captured: list[httpx.Request] = []
    reading, other = uuid.uuid4(), uuid.uuid4()
    async with _pool(_received(captured), DeliveryLimits(share_bytes=1024)) as pool:
        reservation = pool.reserve(reading)
        assert reservation is not None
        assert reservation.grow(512)
        assert pool._shares[reading].reserved == 512
        assert pool._shares[reading].items == 0

        assert not _export(pool, reading, b"y" * 1024)
        assert _export(pool, other, b"y" * 1024)

        assert reservation.grow(512)
        assert pool.admit(reservation, [_delivery(reading, reading, b"x" * 1024)])
        reservation.close()
        await pool.drain()
        assert pool._shares == {}
    assert sorted(len(request.content) for request in captured) == [1024, 1024]


@pytest.mark.anyio
async def test_delivery_retries_transient_failures_and_honours_retry_after() -> None:
    """429/5xx-gateway statuses and transport errors are retried; Retry-After sets the delay."""
    replies: list[httpx.Response | httpx.TransportError] = [
        httpx.Response(429, headers={"retry-after": "0"}),
        httpx.ConnectError("refused"),
        httpx.Response(200),
    ]
    captured: list[httpx.Request] = []

    async def collector(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        reply = replies.pop(0)
        if isinstance(reply, httpx.TransportError):
            raise reply
        return reply

    async with _pool(collector) as pool:
        assert _export(pool, uuid.uuid4())
    assert len(captured) == 3
    assert replies == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "attempts"),
    [(500, 1), (400, 1), (401, 1), (502, 3), (503, 3), (504, 3)],
)
async def test_terminal_statuses_are_not_retried_and_retries_are_bounded(
    status: int, attempts: int
) -> None:
    """A 500 or 4xx ends delivery at once; retryable statuses stop after the attempt budget."""
    captured: list[httpx.Request] = []

    async def collector(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status)

    async with _pool(collector) as pool:
        assert _export(pool, uuid.uuid4())
        await pool.drain()
        assert pool._reserved == 0
    assert len(captured) == attempts


@pytest.mark.parametrize(
    ("status", "value", "delay"),
    [
        (429, "2", 2.0),
        (503, "100", 30.0),
        (502, "2", None),
        (429, "soon", None),
        (429, "Wed, 21 Oct 2015 07:28:00 GMT", None),
        (429, "-1", None),
        (503, None, None),
        # Past float's range: still capped, not an error that ends the delivery.
        (429, "9" * 400, 30.0),
    ],
)
def test_retry_after_is_honoured_only_for_throttling_and_capped(
    status: int, value: str | None, delay: float | None
) -> None:
    """Only a 429 or 503's integer Retry-After is honoured, capped at the limit."""
    assert otel_delivery._retry_after_seconds(status, value, 30.0) == delay


@asynccontextmanager
async def _recorded_sleeps(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[list[float]]:
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def record_sleep(delay: float) -> None:
        slept.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(otel_delivery.asyncio, "sleep", record_sleep)
    yield slept


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("retry_after", "slept_for"),
    [("9", 2.0), ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0)],
    ids=["capped", "http-date-falls-back-to-backoff"],
)
async def test_retry_after_replaces_backoff_up_to_the_cap(
    monkeypatch: pytest.MonkeyPatch, retry_after: str, slept_for: float
) -> None:
    """An integer Retry-After replaces the backoff, capped; any other form leaves it."""
    replies = [httpx.Response(429, headers={"retry-after": retry_after})]

    async def collector(_request: httpx.Request) -> httpx.Response:
        return replies.pop(0) if replies else httpx.Response(200)

    async with _recorded_sleeps(monkeypatch) as slept:
        async with _pool(
            collector, DeliveryLimits(max_retry_after_seconds=2.0)
        ) as pool:
            assert _export(pool, uuid.uuid4())
    assert slept == [slept_for]


@pytest.mark.anyio
async def test_retry_backoff_is_jittered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two retries at the same attempt number do not share a fixed delay."""
    monkeypatch.setattr(otel_delivery, "_RETRY_BASE_DELAY_SECONDS", 1.0)
    statuses = [503, 200, 503, 200]

    async def collector(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(statuses.pop(0))

    async with _recorded_sleeps(monkeypatch) as slept:
        async with _pool(collector) as pool:
            for _ in range(2):
                assert _export(pool, uuid.uuid4())
                await pool.drain()
    assert len(slept) == 2
    # Jittered into [base, 1.5 * base); a deterministic backoff would be exact.
    assert all(1.0 <= delay < 1.5 for delay in slept)
    assert slept[0] != slept[1]


@pytest.mark.anyio
async def test_endless_response_body_is_read_only_up_to_the_cap() -> None:
    """A hostile collector's endless body is abandoned at the cap, and the
    delivery still counts as accepted on its status."""
    stream = _EndlessStream()
    captured: list[httpx.Request] = []

    async def collector(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, stream=stream)

    async with _pool(collector) as pool:
        assert _export(pool, uuid.uuid4())
    assert len(captured) == 1
    cap = otel_delivery._MAX_DISCARDED_RESPONSE_BYTES
    assert cap < stream.pulled <= cap + len(_EndlessStream.chunk)
    assert stream.closed


@asynccontextmanager
async def _keepalive_collector(
    reply: bytes,
) -> AsyncIterator[tuple[str, list[int]]]:
    """A TCP collector that answers every request with `reply` as its body and
    keeps the connection open for the next request."""
    connections: list[int] = []

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        connections.append(len(connections) + 1)
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                await reader.readexactly(length)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(reply)
                    + reply
                )
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/v1/traces", connections
    finally:
        server.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("reply", "connections_used"),
    [(b"{}", 1), (b"x" * (2 * otel_delivery._MAX_DISCARDED_RESPONSE_BYTES), 2)],
    ids=["small-reply-reuses-connection", "oversized-reply-closes-connection"],
)
async def test_small_replies_return_their_connection_to_the_pool(
    reply: bytes, connections_used: int
) -> None:
    """A collector's small reply is read to its end so the next delivery reuses the
    connection; a reply past the cap closes it instead."""
    organization_id = uuid.uuid4()
    async with (
        _keepalive_collector(reply) as (url, connections),
        collector_client(trust_env=False) as client,
    ):
        pool = OtelDeliveryPool(client)
        for _ in range(2):
            assert _export(pool, organization_id, url=url)
            await asyncio.wait_for(pool.drain(), timeout=5)
        assert connections == list(range(1, connections_used + 1))


@pytest.mark.anyio
async def test_collector_redirects_are_not_followed() -> None:
    """A redirect ends the delivery instead of re-posting the batch elsewhere."""
    captured: list[httpx.Request] = []

    async def collector(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            307, headers={"location": "https://elsewhere.example.com/v1/traces"}
        )

    async with _pool(collector) as pool:
        assert _export(pool, uuid.uuid4())
    assert [str(request.url) for request in captured] == [COLLECTOR]


@pytest.mark.anyio
async def test_collector_cookies_never_reach_a_later_delivery() -> None:
    """A collector's Set-Cookie is refused, so no delivery, for any org, carries a cookie."""
    captured: list[httpx.Request] = []

    async def collector(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200, headers={"set-cookie": "collector-session=synthetic-org-a; Path=/"}
        )

    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    async with _pool(collector) as pool:
        for organization_id in (org_a, org_b, org_a):
            assert _export(pool, organization_id)
            await pool.drain()
        assert len(pool.client.cookies.jar) == 0
    assert [request.headers["authorization"] for request in captured] == [
        f"Bearer {org_a}",
        f"Bearer {org_b}",
        f"Bearer {org_a}",
    ]
    assert [request.headers.get("cookie") for request in captured] == [None] * 3


@pytest.mark.anyio
async def test_drain_lets_pending_deliveries_finish() -> None:
    """Shutdown waits for an admitted export to reach its collector."""
    release = asyncio.Event()
    captured: list[httpx.Request] = []
    async with _pool(_released(release, captured)) as pool:
        assert _export(pool, uuid.uuid4())
        draining = asyncio.create_task(pool.drain())
        await asyncio.sleep(0.05)
        assert not draining.done()
        release.set()
        await draining
    assert len(captured) == 1


@pytest.mark.anyio
async def test_drain_cancels_deliveries_still_pending_after_its_bound() -> None:
    """A collector that never answers cannot hold shutdown past the drain bound."""

    async def collector(_request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        return httpx.Response(200)

    async with _pool(collector, DeliveryLimits(drain_seconds=0.05)) as pool:
        assert _export(pool, uuid.uuid4())
        await asyncio.wait_for(pool.drain(), timeout=5)
        assert pool._tasks == {}
        assert pool._reserved == 0
        assert pool._shares == {}


def _stalls(organization_id: uuid.UUID) -> tuple[Collector, asyncio.Event]:
    """A collector that never answers this org, and an event set once another
    org's export is delivered."""
    delivered = asyncio.Event()

    async def collector(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == f"Bearer {organization_id}":
            await asyncio.Event().wait()
        delivered.set()
        return httpx.Response(200)

    return collector, delivered


@pytest.mark.anyio
@pytest.mark.parametrize("budget", ["bytes", "exports"])
async def test_stalled_org_cannot_make_another_orgs_small_export_fail(
    budget: str,
) -> None:
    """An org whose collector stalls fills only its share of the pool, whether with
    large exports or with many exports; another org's small export still goes out."""
    stalled, other = uuid.uuid4(), uuid.uuid4()
    collector, delivered = _stalls(stalled)
    # As with the defaults, each share is a quarter of the pool, so four of
    # these exports would fill it.
    large = b"x" * 1024
    body = large if budget == "bytes" else BODY
    limits = DeliveryLimits(
        max_items=4,
        max_bytes=4 * len(large),
        share_items=1 if budget == "exports" else 4,
        share_bytes=len(large) if budget == "bytes" else 4 * len(large),
        drain_seconds=0.05,
    )
    async with _pool(collector, limits) as pool:
        assert [_export(pool, stalled, body) for _ in range(4)] == [
            True,
            False,
            False,
            False,
        ]
        assert _export(pool, other)
        await asyncio.wait_for(delivered.wait(), timeout=5)
        await pool.drain()
        assert pool._shares == {}
        assert pool._reserved == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("stall", "posts"),
    [("hang", 2), ("throttle", 1)],
    ids=["slow-collector-takes-only-its-share", "backoff-holds-no-shared-slot"],
)
async def test_one_orgs_stalled_collector_leaves_slots_for_other_orgs(
    stall: str, posts: int
) -> None:
    """An org's deliveries hold at most its share of the slots, and none while backing off."""
    stalled, other = uuid.uuid4(), uuid.uuid4()
    delivered = asyncio.Event()

    async def collector(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] != f"Bearer {stalled}":
            delivered.set()
            return httpx.Response(200)
        if stall == "hang":
            await asyncio.Event().wait()
        return httpx.Response(429, headers={"retry-after": "30"})

    limits = DeliveryLimits(posts=posts, share_posts=1, drain_seconds=0.05)
    async with _pool(collector, limits) as pool:
        for organization_id in (stalled, stalled, other):
            assert _export(pool, organization_id)
        await asyncio.wait_for(delivered.wait(), timeout=5)
        await pool.drain()
        assert pool._shares == {}


@asynccontextmanager
async def _busy_collector() -> AsyncIterator[tuple[str, list[int]]]:
    """A TCP collector that answers every request with `100 Continue` forever,
    keeping the connection active, so HTTPX's inactivity timeouts never fire."""
    connections: list[int] = []

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        connections.append(len(connections) + 1)
        try:
            await reader.readuntil(b"\r\n\r\n")
            # uvloop raises RuntimeError, not ConnectionError, on a write to a
            # transport the client already closed.
            while not writer.is_closing():
                writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                await writer.drain()
                await asyncio.sleep(0.02)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/v1/traces", connections
    finally:
        server.close()


@pytest.mark.anyio
async def test_attempt_is_cut_at_its_deadline_while_the_collector_stays_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each attempt ends at its wall-clock deadline, even on a connection that never idles."""
    warning = MagicMock()
    monkeypatch.setattr(otel_delivery.logger, "warning", warning)
    organization_id = uuid.uuid4()
    async with (
        _busy_collector() as (url, connections),
        collector_client(trust_env=False) as client,
    ):
        pool = OtelDeliveryPool(
            client, DeliveryLimits(attempts=2, attempt_seconds=0.2, drain_seconds=5)
        )
        reservation = pool.reserve(organization_id, len(BODY))
        assert reservation is not None
        assert pool.admit(
            reservation, [_delivery(organization_id, organization_id, url=url)]
        )
        reservation.close()
        started = time.monotonic()
        # HTTPX's own 10-second timeouts would never end these attempts.
        await asyncio.wait_for(pool.drain(), timeout=5)
        assert time.monotonic() - started >= 0.4
        assert connections == [1, 2]
    warning.assert_called_once()
    assert warning.call_args.kwargs["error_type"] == "TimeoutError"
    assert warning.call_args.kwargs["attempts"] == 2
