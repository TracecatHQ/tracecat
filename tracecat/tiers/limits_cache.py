"""Redis cache for organization effective tier limits."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from tracecat import config
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.tiers.schemas import EffectiveLimits

if TYPE_CHECKING:
    from tracecat.identifiers import OrganizationID


def effective_limits_cache_key(org_id: OrganizationID) -> str:
    """Build the Redis key for an organization's effective limits cache."""
    return f"tier:org:{org_id}:effective-limits:v1"


async def get_effective_limits_cached(org_id: OrganizationID) -> EffectiveLimits | None:
    """Read effective limits from Redis cache."""
    ttl_seconds = config.TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS
    if ttl_seconds <= 0:
        return None

    redis_client = await get_redis_client()
    client = await redis_client._get_client()
    key = effective_limits_cache_key(org_id)
    payload = await client.get(key)
    if payload is None:
        return None

    try:
        return EffectiveLimits.model_validate_json(payload)
    except Exception:
        logger.warning(
            "Invalid effective limits cache payload, dropping key",
            org_id=org_id,
            key=key,
        )
        await client.delete(key)
        return None


async def set_effective_limits_cached(
    org_id: OrganizationID, limits: EffectiveLimits
) -> None:
    """Write effective limits to Redis cache."""
    ttl_seconds = config.TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS
    if ttl_seconds <= 0:
        return

    redis_client = await get_redis_client()
    await redis_client.set(
        effective_limits_cache_key(org_id),
        limits.model_dump_json(),
        expire_seconds=ttl_seconds,
    )


async def invalidate_effective_limits_cache(org_id: OrganizationID) -> None:
    """Delete an organization's effective limits cache entry."""
    redis_client = await get_redis_client()
    await redis_client.delete(effective_limits_cache_key(org_id))


async def invalidate_effective_limits_cache_many(
    org_ids: Sequence[OrganizationID],
) -> None:
    """Delete cache entries for multiple organizations."""
    if not org_ids:
        return

    redis_client = await get_redis_client()
    client = await redis_client._get_client()
    keys = [effective_limits_cache_key(org_id) for org_id in org_ids]
    await client.delete(*keys)
