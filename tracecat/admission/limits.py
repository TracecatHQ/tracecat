"""Resolve per-organization concurrency limits with a short in-process cache.

Concurrent cache misses for one organization share a single database read. When
a refresh fails or times out, the last limit read is served for at most one more
TTL; after that the caller gets ``LimitUnavailableError`` and defers the work.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Final

from tracecat import config
from tracecat.admission.types import ConcurrencyResource
from tracecat.db.engine import get_async_session_tenant_context_manager
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.tiers.service import TierService

LOOKUP_TIMEOUT_SECONDS: Final = 1.0
"""How long one caller waits for a limit lookup, separate from the Redis budget."""

READ_TIMEOUT_SECONDS: Final = 10.0
"""Ceiling for the shared database read, so a stalled read can't pin an org's
lookups or hold a connection; the next caller then starts a fresh read."""


class LimitUnavailableError(Exception):
    """The limit couldn't be read and no recent enough cached value exists."""


@dataclass(frozen=True, slots=True)
class _CachedLimits:
    limits: EffectiveLimits
    fetched_at: float


_cache: dict[OrganizationID, _CachedLimits] = {}
_in_flight: dict[OrganizationID, asyncio.Task[EffectiveLimits]] = {}


async def _read(org_id: OrganizationID) -> EffectiveLimits:
    # Activities run without a ctx_role; scope the read to the org explicitly.
    async with (
        asyncio.timeout(READ_TIMEOUT_SECONDS),
        get_async_session_tenant_context_manager(organization_id=org_id) as session,
    ):
        limits = await TierService(session).get_effective_limits(org_id)
    if config.TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS > 0:
        _cache[org_id] = _CachedLimits(limits=limits, fetched_at=monotonic())
    return limits


def _on_read_done(org_id: OrganizationID, task: asyncio.Task[EffectiveLimits]) -> None:
    if _in_flight.get(org_id) is task:
        del _in_flight[org_id]
    if not task.cancelled():
        # Mark the error retrieved even if every waiter timed out.
        task.exception()


async def _load(org_id: OrganizationID) -> EffectiveLimits:
    """Read an organization's limits; concurrent callers share one read.

    A caller that times out or is cancelled leaves the read running for the
    others.
    """
    task = _in_flight.get(org_id)
    if task is None or task.get_loop() is not asyncio.get_running_loop():
        task = asyncio.create_task(_read(org_id))
        _in_flight[org_id] = task
        task.add_done_callback(lambda t: _on_read_done(org_id, t))
    return await asyncio.shield(task)


def _fresh(org_id: OrganizationID, now: float) -> _CachedLimits | None:
    cached = _cache.get(org_id)
    ttl_seconds = config.TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS
    if cached is not None and now < cached.fetched_at + ttl_seconds:
        return cached
    return None


async def get_effective_limits(org_id: OrganizationID) -> EffectiveLimits:
    """Return an organization's effective limits, cached per process.

    The cache lives for ``TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS`` (30 s by
    default), so a changed limit applies within that long; 0 disables it.

    Raises:
        Exception: Any lookup failure. Callers decide how to degrade.
    """
    if (cached := _fresh(org_id, monotonic())) is not None:
        return cached.limits
    return await _load(org_id)


def limit_for(limits: EffectiveLimits, resource: ConcurrencyResource) -> int | None:
    """Pick one resource's limit from an organization's effective limits."""
    match resource:
        case ConcurrencyResource.ACTIONS:
            return limits.max_concurrent_actions
        case ConcurrencyResource.WORKFLOWS:
            return limits.max_concurrent_workflows
        case ConcurrencyResource.TOOL_CALLS:
            return limits.max_concurrent_tool_calls
        case ConcurrencyResource.AGENT_TURNS:
            return limits.max_concurrent_agent_turns


async def resolve_limit(
    org_id: OrganizationID,
    resource: ConcurrencyResource,
    *,
    timeout_seconds: float = LOOKUP_TIMEOUT_SECONDS,
) -> int | None:
    """Return an organization's limit for one resource; ``None`` means unlimited.

    Raises:
        LimitUnavailableError: The lookup failed or timed out, and the cached
            limit is older than two TTLs (or there is none).
    """
    now = monotonic()
    if (cached := _fresh(org_id, now)) is not None:
        return limit_for(cached.limits, resource)
    try:
        async with asyncio.timeout(timeout_seconds):
            limits = await _load(org_id)
    except Exception as e:
        # The lookup took time: judge staleness now, not when it started.
        now = monotonic()
        stale = _cache.get(org_id)
        ttl_seconds = config.TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS
        if stale is not None and now < stale.fetched_at + 2 * ttl_seconds:
            logger.warning(
                "concurrency.stale_limit",
                org_id=str(org_id),
                resource=resource.value,
                age_seconds=now - stale.fetched_at,
                error=repr(e),
            )
            return limit_for(stale.limits, resource)
        raise LimitUnavailableError(
            f"Could not read the {resource.value} limit: {e!r}"
        ) from e
    return limit_for(limits, resource)


def clear_limits_cache() -> None:
    """Drop every cached entry (tests)."""
    _cache.clear()
    _in_flight.clear()
