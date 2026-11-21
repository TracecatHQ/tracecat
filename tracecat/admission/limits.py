"""Resolve per-organization concurrency limits with a short in-process cache."""

from __future__ import annotations

import time
from dataclasses import dataclass

from tracecat import config
from tracecat.admission.types import ConcurrencyResource
from tracecat.db.engine import get_async_session_tenant_context_manager
from tracecat.identifiers import OrganizationID
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.tiers.service import TierService


@dataclass(frozen=True, slots=True)
class _CachedLimits:
    limits: EffectiveLimits
    expires_at: float


_cache: dict[OrganizationID, _CachedLimits] = {}


async def get_effective_limits(org_id: OrganizationID) -> EffectiveLimits:
    """Return an organization's effective limits, cached per process.

    The cache lives for ``TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS`` (30 s by
    default), so a changed limit applies within that long; 0 disables it.

    Raises:
        Exception: Any lookup failure. Callers decide how to degrade.
    """
    now = time.monotonic()
    ttl_seconds = config.TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS
    if (cached := _cache.get(org_id)) is not None:
        if cached.expires_at > now:
            return cached.limits
        del _cache[org_id]

    # Activities run without a ctx_role; scope the read to the org explicitly.
    async with get_async_session_tenant_context_manager(
        organization_id=org_id
    ) as session:
        limits = await TierService(session).get_effective_limits(org_id)

    if ttl_seconds > 0:
        _cache[org_id] = _CachedLimits(limits=limits, expires_at=now + ttl_seconds)
    return limits


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
    org_id: OrganizationID, resource: ConcurrencyResource
) -> int | None:
    """Return an organization's limit for one resource; ``None`` means unlimited."""
    return limit_for(await get_effective_limits(org_id), resource)


def clear_limits_cache() -> None:
    """Drop every cached entry (tests)."""
    _cache.clear()
