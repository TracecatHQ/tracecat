"""Per-organization pacing of workflow starts.

Each start reserves the next slot in its org's schedule (GCRA, one Redis Lua
call). The first ``burst`` starts go at once; later ones are spaced at the
org's rate by delaying the run inside Temporal (``start_delay``), so the
caller holds nothing while it waits. Every start is accepted, however long
its delay: refusing work belongs at ingest, not here. Each delayed start logs
``concurrency.start_paced`` with its org and delay, so long waits are visible.

With no rate set, Tracecat doesn't pace and makes no Redis call. If Redis or
the limit lookup fails, the start goes through at once.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from tracecat import config
from tracecat.db.engine import get_async_session_tenant_context_manager
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.tiers.service import TierService

if TYPE_CHECKING:
    from tracecat.identifiers import OrganizationID
    from tracecat.tiers.schemas import EffectiveLimits

_RESOURCE = "workflow_starts"
_REDIS_TIMEOUT_SECONDS = 1.0
# The rate lookup opens its own DB session while the caller may still hold
# one. Bound it, and remember a failure briefly, so an exhausted pool stalls
# at most one lookup per org per window instead of every start.
_LIMIT_LOOKUP_TIMEOUT_SECONDS = 2.0
_FAILED_LOOKUP_CACHE_SECONDS = 5.0
_MICROS_PER_SECOND = 1_000_000

# KEYS[1] = pacing key (holds the org's theoretical arrival time, in µs)
# ARGV[1] = emission interval (µs), ARGV[2] = burst tolerance (µs)
# Returns {delay_us, new_tat_us}.
# Redis TIME keeps every API replica on one clock. Values stay in µs integers
# (below 2^53) and are written with %.0f so Lua's number formatting keeps
# every digit.
_RESERVE_SCRIPT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000000 + tonumber(t[2])
local interval = tonumber(ARGV[1])
local tolerance = tonumber(ARGV[2])

local tat = tonumber(redis.call('GET', KEYS[1]) or now)
if tat < now then
    tat = now
end

local delay = tat - tolerance - now
if delay < 0 then
    delay = 0
end

local new_tat = tat + interval
local ttl_ms = math.ceil((new_tat - now) / 1000) + 1000
redis.call('SET', KEYS[1], string.format('%.0f', new_tat), 'PX', ttl_ms)
return {delay, new_tat}
"""

# Undo a reservation, only while it is still the org's latest: move the
# theoretical arrival time back one interval. Once a later start has reserved,
# its delay is already with Temporal, and moving the time back would hand the
# same slot out twice, so the refund is skipped.
# KEYS[1] = pacing key, ARGV[1] = emission interval (µs), ARGV[2] = the
# arrival time the reservation set (µs). Returns 1 when refunded, else 0.
_REFUND_SCRIPT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000000 + tonumber(t[2])
if redis.call('GET', KEYS[1]) ~= ARGV[2] then
    return 0
end
local new_tat = tonumber(ARGV[2]) - tonumber(ARGV[1])
if new_tat <= now then
    redis.call('DEL', KEYS[1])
    return 1
end
local ttl_ms = math.ceil((new_tat - now) / 1000) + 1000
redis.call('SET', KEYS[1], string.format('%.0f', new_tat), 'PX', ttl_ms)
return 1
"""


@dataclass(frozen=True, slots=True)
class StartSlot:
    """A reserved start: how long it waits, and what a refund needs.

    ``tat_us`` and ``interval_us`` are set only when Redis recorded the
    reservation; an unpaced or fail-open start has nothing to refund.
    """

    delay: timedelta
    tat_us: int | None = None
    interval_us: int | None = None
    reserved_at: float = field(default_factory=time.monotonic)
    """When the slot was reserved, on this process's monotonic clock."""

    def remaining_delay(self) -> timedelta:
        """The delay still owed when the start is handed to Temporal.

        Temporal counts ``start_delay`` from submission, so the time spent
        since the reservation (storing trigger inputs, for example) comes off
        it. Otherwise slow submissions would collapse into a burst.
        """
        if not self.delay:
            return self.delay
        elapsed = timedelta(seconds=time.monotonic() - self.reserved_at)
        return max(timedelta(0), self.delay - elapsed)


UNPACED = StartSlot(delay=timedelta(0))


@dataclass(frozen=True, slots=True)
class StartRate:
    """An org's start rate: ``per_second`` steady, ``burst`` at once."""

    per_second: int
    burst: int


@dataclass(frozen=True, slots=True)
class _CachedRate:
    expires_at: float
    rate: StartRate | None


_rate_cache: dict[OrganizationID, _CachedRate] = {}
# One lookup per org at a time; concurrent cold starts wait on it.
_rate_lookups: dict[OrganizationID, asyncio.Task[StartRate | None]] = {}


def start_rate_from_limits(limits: EffectiveLimits) -> StartRate | None:
    """Build the start rate from tier limits; ``None`` means unpaced.

    ``api_rate_limit`` is starts per second. ``api_burst_capacity`` defaults
    to one second of starts when unset.
    """
    per_second = limits.api_rate_limit
    if per_second is None:
        return None
    if per_second <= 0:
        logger.warning(
            "Ignoring a non-positive workflow start rate", api_rate_limit=per_second
        )
        return None
    burst = limits.api_burst_capacity
    if burst is None or burst <= 0:
        burst = per_second
    return StartRate(per_second=per_second, burst=burst)


async def resolve_start_rate(org_id: OrganizationID) -> StartRate | None:
    """Return the org's start rate, cached in process; ``None`` means unpaced."""
    if not config.TRACECAT__EE_MULTI_TENANT:
        return None
    cached = _rate_cache.get(org_id)
    if cached is not None and cached.expires_at > time.monotonic():
        return cached.rate
    if (lookup := _rate_lookups.get(org_id)) is None:
        lookup = asyncio.create_task(_load_start_rate(org_id))
        _rate_lookups[org_id] = lookup
        lookup.add_done_callback(lambda done: _forget_lookup(org_id, done))
    # A caller that gives up must not cancel the lookup others wait on.
    return await asyncio.shield(lookup)


def _forget_lookup(
    org_id: OrganizationID, done: asyncio.Task[StartRate | None]
) -> None:
    if _rate_lookups.get(org_id) is done:
        del _rate_lookups[org_id]


async def _load_start_rate(org_id: OrganizationID) -> StartRate | None:
    try:
        async with asyncio.timeout(_LIMIT_LOOKUP_TIMEOUT_SECONDS):
            async with get_async_session_tenant_context_manager(
                organization_id=org_id
            ) as session:
                limits = await TierService(session).get_effective_limits(org_id)
    except Exception as e:
        logger.warning(
            "concurrency.fail_open",
            resource=_RESOURCE,
            org_id=str(org_id),
            reason="limit_lookup_failed",
            error_type=type(e).__name__,
        )
        _rate_cache[org_id] = _CachedRate(
            expires_at=time.monotonic() + _FAILED_LOOKUP_CACHE_SECONDS, rate=None
        )
        return None
    rate = start_rate_from_limits(limits)
    ttl = config.TRACECAT__WORKFLOW_START_PACING_LIMITS_CACHE_SECONDS
    _rate_cache[org_id] = _CachedRate(expires_at=time.monotonic() + ttl, rate=rate)
    return rate


def _pacing_key(org_id: OrganizationID) -> str:
    return f"concurrency:{org_id}:{_RESOURCE}"


def _interval_us(rate: StartRate) -> int:
    return max(1, round(_MICROS_PER_SECOND / rate.per_second))


class StartPacer:
    """Paces workflow starts per org."""

    async def reserve(self, org_id: OrganizationID) -> StartSlot:
        """Reserve a start slot; its ``delay`` is how long the start waits.

        Never refuses: a start past the burst waits as long as the org's rate
        requires.
        """
        if (rate := await resolve_start_rate(org_id)) is None:
            return UNPACED
        interval_us = _interval_us(rate)
        tolerance_us = (rate.burst - 1) * interval_us
        try:
            async with asyncio.timeout(_REDIS_TIMEOUT_SECONDS):
                redis_client = await get_redis_client()
                client = await redis_client._get_client()
                script = client.register_script(_RESERVE_SCRIPT)
                value_us, new_tat_us = await script(
                    keys=[_pacing_key(org_id)],
                    args=[interval_us, tolerance_us],
                )
        except Exception as e:
            # Any Redis-side failure, including client setup (URL secret,
            # connection pool), fails open: the start goes through at once.
            logger.warning(
                "concurrency.fail_open",
                resource=_RESOURCE,
                org_id=str(org_id),
                reason="redis_unavailable",
                error_type=type(e).__name__,
            )
            return UNPACED

        value = timedelta(microseconds=int(value_us))
        if value:
            # One event per delayed start, so a queue-age alert can watch it.
            logger.info(
                "concurrency.start_paced",
                resource=_RESOURCE,
                org_id=str(org_id),
                limit=rate.per_second,
                burst=rate.burst,
                delay_seconds=value.total_seconds(),
            )
        return StartSlot(delay=value, tat_us=int(new_tat_us), interval_us=interval_us)

    async def refund(self, org_id: OrganizationID, slot: StartSlot) -> None:
        """Give back a reserved slot whose start didn't create a run.

        Call it only when the start certainly made no run, such as Temporal
        rejecting a duplicate execution ID. The slot comes back only while no
        later start has reserved. Failures are logged, not raised.
        """
        if slot.tat_us is None or slot.interval_us is None:
            return
        try:
            async with asyncio.timeout(_REDIS_TIMEOUT_SECONDS):
                redis_client = await get_redis_client()
                client = await redis_client._get_client()
                script = client.register_script(_REFUND_SCRIPT)
                await script(
                    keys=[_pacing_key(org_id)],
                    args=[slot.interval_us, slot.tat_us],
                )
        except Exception as e:
            logger.warning(
                "concurrency.refund_failed",
                resource=_RESOURCE,
                org_id=str(org_id),
                error_type=type(e).__name__,
            )
