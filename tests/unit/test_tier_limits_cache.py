from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from tracecat import config
from tracecat.tiers.limits_cache import (
    effective_limits_cache_key,
    get_effective_limits_cached,
    invalidate_effective_limits_cache_many,
    set_effective_limits_cached,
)
from tracecat.tiers.schemas import EffectiveLimits


class _FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted_keys: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.values:
                removed += 1
                del self.values[key]
            self.deleted_keys.append(key)
        return removed


class _FakeRedisWrapper:
    def __init__(self, client: _FakeRedisClient) -> None:
        self._client = client
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def _get_client(self) -> _FakeRedisClient:
        return self._client

    async def set(
        self, key: str, value: str, *, expire_seconds: int | None = None
    ) -> bool:
        self._client.values[key] = value
        self.set_calls.append((key, value, expire_seconds))
        return True


def _limits(
    *, workflows: int | None = None, actions: int | None = None
) -> EffectiveLimits:
    return EffectiveLimits(
        api_rate_limit=None,
        api_burst_capacity=None,
        max_concurrent_workflows=workflows,
        max_action_executions_per_workflow=None,
        max_concurrent_actions=actions,
    )


@pytest.mark.anyio
async def test_effective_limits_cache_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    client = _FakeRedisClient()
    wrapper = _FakeRedisWrapper(client)
    monkeypatch.setattr(config, "TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS", 30)
    monkeypatch.setattr(
        "tracecat.tiers.limits_cache.get_redis_client",
        AsyncMock(return_value=wrapper),
    )

    limits = _limits(workflows=3, actions=2)
    await set_effective_limits_cached(org_id, limits)
    cached = await get_effective_limits_cached(org_id)

    assert cached == limits
    assert wrapper.set_calls[0][0] == effective_limits_cache_key(org_id)
    assert wrapper.set_calls[0][2] == 30

    await invalidate_effective_limits_cache_many([org_id])
    assert await get_effective_limits_cached(org_id) is None


@pytest.mark.anyio
async def test_effective_limits_cache_drops_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    client = _FakeRedisClient()
    wrapper = _FakeRedisWrapper(client)
    key = effective_limits_cache_key(org_id)
    client.values[key] = "{invalid-json"
    monkeypatch.setattr(config, "TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS", 30)
    monkeypatch.setattr(
        "tracecat.tiers.limits_cache.get_redis_client",
        AsyncMock(return_value=wrapper),
    )

    cached = await get_effective_limits_cached(org_id)

    assert cached is None
    assert key in client.deleted_keys


@pytest.mark.anyio
async def test_effective_limits_cache_respects_disabled_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    client = _FakeRedisClient()
    wrapper = _FakeRedisWrapper(client)
    monkeypatch.setattr(config, "TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS", 0)
    monkeypatch.setattr(
        "tracecat.tiers.limits_cache.get_redis_client",
        AsyncMock(return_value=wrapper),
    )

    await set_effective_limits_cached(org_id, _limits(actions=5))

    assert wrapper.set_calls == []
    assert await get_effective_limits_cached(org_id) is None
