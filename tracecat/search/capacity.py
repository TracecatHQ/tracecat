"""Atomic Redis leases shared by all indexing and foreground replicas."""

from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import cast
from uuid import uuid4

from opentelemetry import metrics

from tracecat.redis.client import RedisClient
from tracecat.search.types import SearchScope

# Use Redis time and one hash tag so acquisition is atomic on Redis Cluster too.
_ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + tonumber(t[2]) / 1000
for i, key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
  if redis.call('ZCARD', key) >= tonumber(ARGV[i + 2]) then return 0 end
end
for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, now + tonumber(ARGV[2]), ARGV[1])
  redis.call('PEXPIRE', key, tonumber(ARGV[2]) * 2)
end
return 1
"""
_RELEASE = """
for _, key in ipairs(KEYS) do redis.call('ZREM', key, ARGV[1]) end
return 1
"""


@asynccontextmanager
async def search_capacity(
    scope: SearchScope, *, background: bool = False
) -> AsyncIterator[bool]:
    """Reserve capacity, fail closed on Redis errors, expire crashed callers.

    Background work is bounded by a 90s deadline, foreground by a 30s provider
    deadline. The 120s lease outlives both. Six background jobs (one/workspace)
    reserve two global slots and one workspace slot for foreground requests.
    Background jobs hold their permit across DB work as well as provider I/O.
    """
    prefix = "{semantic-search}:capacity:"
    workspace = f"{scope.organization_id}:{scope.workspace_id}"
    keys = [prefix + "global", prefix + workspace]
    limits = [8, 2]
    if background:
        keys += [prefix + "background", prefix + "background:" + workspace]
        limits += [6, 1]
    client = await RedisClient()._get_client()
    token = uuid4().hex
    acquired = bool(
        await cast(
            Awaitable[object],
            client.eval(_ACQUIRE, len(keys), *keys, token, 120000, *limits),
        )
    )
    meter = metrics.get_meter("tracecat.search")
    tags = {"lane": "background" if background else "foreground"}
    active = meter.create_up_down_counter("search.capacity.active")
    if acquired:
        active.add(1, tags)
    else:
        meter.create_counter("search.capacity.deferred").add(1, tags)
    try:
        yield acquired
    finally:
        if acquired:
            active.add(-1, tags)
            await cast(
                Awaitable[object], client.eval(_RELEASE, len(keys), *keys, token)
            )
