"""Resolve per-organization concurrency limits with a short in-process cache."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from tracecat.admission.types import ConcurrencyResource
from tracecat.db.engine import get_async_session_tenant_context_manager
from tracecat.identifiers import OrganizationID
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.tiers.service import TierService

LIMITS_CACHE_TTL_SECONDS: Final = 30.0
"""How long a process reuses an organization's resolved limits."""


@dataclass(frozen=True, slots=True)
class _CachedLimits:
    limits: EffectiveLimits
    expires_at: float


_cache: dict[OrganizationID, _CachedLimits] = {}


async def get_effective_limits(org_id: OrganizationID) -> EffectiveLimits:
    """Return an organization's effective limits, cached for 30 s per process.

    Raises:
        Exception: Any lookup failure. Callers decide how to degrade.
    """
    now = time.monotonic()
    if (cached := _cache.get(org_id)) is not None and cached.expires_at > now:
        return cached.limits

    # Activities run without a ctx_role; scope the read to the org explicitly.
    async with get_async_session_tenant_context_manager(
        organization_id=org_id
    ) as session:
        limits = await TierService(session).get_effective_limits(org_id)

    _cache[org_id] = _CachedLimits(
        limits=limits, expires_at=now + LIMITS_CACHE_TTL_SECONDS
    )
    return limits


def limit_for(limits: EffectiveLimits, resource: ConcurrencyResource) -> int | None:
    """Pick one resource's limit from an organization's effective limits."""
    match resource:
        case ConcurrencyResource.ACTIONS:
            return limits.max_concurrent_actions
        case ConcurrencyResource.WORKFLOWS:
            return limits.max_concurrent_workflows
        case ConcurrencyResource.TOOL_CALLS | ConcurrencyResource.AGENT_TURNS:
            # Limits for these arrive with ENG-2564.
            return None


async def resolve_limit(
    org_id: OrganizationID, resource: ConcurrencyResource
) -> int | None:
    """Return an organization's limit for one resource; ``None`` means unlimited."""
    return limit_for(await get_effective_limits(org_id), resource)


def clear_limits_cache() -> None:
    """Drop every cached entry (tests)."""
    _cache.clear()
