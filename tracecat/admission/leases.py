"""Redis lease store for per-organization concurrency limits.

Each organization and resource has one sorted set. A lease of ``units`` adds
the members ``<lease_id>#0`` to ``<lease_id>#<units - 1>``; each member's score
is its expiry in Redis server milliseconds. The count in use is the set size
after expired members are pruned.

Beside it sits a waiter set: one entry per run blocked at the limit, scored by
when it joined (Redis microseconds). A refused acquire that names a waiter adds it; a release
that freed units pops the oldest so the executor can wake that run. The waiter
set never limits anything: the lease set stays the only limit.

The scripts read the clock with Redis ``TIME``, so client clocks don't matter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self

from tracecat.admission.types import Admitted, Busy, ConcurrencyResource
from tracecat.redis.client import get_redis_client

if TYPE_CHECKING:
    import redis.asyncio as redis
    from redis.commands.core import AsyncScript

    from tracecat.identifiers import OrganizationID

MAX_LEASE_TTL_SECONDS: Final = 90.0
"""Longest a lease lives without renewal; bounds how long a crash holds units."""

_KEY_TTL_MS: Final = int(MAX_LEASE_TTL_SECONDS * 1000) * 2
"""Expiry of the whole set, refreshed on every write, so idle keys disappear."""

# Shared prelude: Redis server time in ms, then prune expired members.
_PRELUDE = """
local key = KEYS[1]
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
"""

# KEYS: lease set, waiter set
# ARGV: lease_id, units, limit, ttl_ms, key_ttl_ms, waiter ('' for none)
# Returns {admitted (0|1), in_use}. A refusal adds the waiter if it isn't queued.
_ACQUIRE_SCRIPT = (
    _PRELUDE
    + """
local lease_id = ARGV[1]
local units = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local expires = now + tonumber(ARGV[4])
local key_ttl = tonumber(ARGV[5])
local waiter = ARGV[6]

local held = redis.call('ZSCORE', key, lease_id .. '#0')
local count = redis.call('ZCARD', key)
if not held and count + units > limit then
    if waiter ~= '' then
        -- Scored in microseconds, so runs queue in the order they were refused.
        local joined = tonumber(t[1]) * 1000000 + tonumber(t[2])
        redis.call('ZADD', KEYS[2], 'NX', joined, waiter)
        redis.call('PEXPIRE', KEYS[2], key_ttl)
    end
    return {0, count}
end
for i = 0, units - 1 do
    redis.call('ZADD', key, expires, lease_id .. '#' .. i)
end
redis.call('PEXPIRE', key, key_ttl)
return {1, redis.call('ZCARD', key)}
"""
)

# ARGV: lease_id, units, ttl_ms, key_ttl_ms
# Returns 1 if the lease was still held and is now extended, else 0.
_RENEW_SCRIPT = (
    _PRELUDE
    + """
local lease_id = ARGV[1]
local units = tonumber(ARGV[2])
local expires = now + tonumber(ARGV[3])
local key_ttl = tonumber(ARGV[4])

if not redis.call('ZSCORE', key, lease_id .. '#0') then
    return 0
end
for i = 0, units - 1 do
    redis.call('ZADD', key, 'XX', expires, lease_id .. '#' .. i)
end
redis.call('PEXPIRE', key, key_ttl)
return 1
"""
)

# KEYS: lease set, waiter set
# ARGV: the lease's members
# Returns the oldest waiter if a member was removed, else nil. A release that
# removed nothing (a duplicate, or a lease already pruned) wakes nobody.
_RELEASE_SCRIPT = """
if redis.call('ZREM', KEYS[1], unpack(ARGV)) == 0 then
    return false
end
local popped = redis.call('ZPOPMIN', KEYS[2], 1)
return popped[1] or false
"""

# Returns the number of unexpired members.
_IN_USE_SCRIPT = (
    _PRELUDE
    + """
return redis.call('ZCARD', key)
"""
)


def lease_key(org_id: OrganizationID, resource: ConcurrencyResource) -> str:
    """Redis key of an organization's lease set for one resource."""
    return f"concurrency:{org_id}:{resource.value}"


def waiter_key(org_id: OrganizationID, resource: ConcurrencyResource) -> str:
    """Redis key of the runs blocked at an organization's limit for one resource."""
    return f"{lease_key(org_id, resource)}:waiters"


@dataclass(frozen=True, slots=True)
class Waiter:
    """A run blocked at the limit, to wake when units free."""

    workflow_id: str
    run_id: str

    def member(self) -> str:
        # Run IDs are UUIDs, so the first colon separates them from the workflow ID.
        return f"{self.run_id}:{self.workflow_id}"

    @classmethod
    def from_member(cls, member: str | bytes) -> Self | None:
        text = member.decode() if isinstance(member, bytes) else member
        run_id, sep, workflow_id = text.partition(":")
        if not sep or not run_id or not workflow_id:
            return None
        return cls(workflow_id=workflow_id, run_id=run_id)


def _members(lease_id: str, units: int) -> list[str]:
    return [f"{lease_id}#{i}" for i in range(units)]


def _ttl_ms(ttl_seconds: float) -> int:
    return max(1, int(min(ttl_seconds, MAX_LEASE_TTL_SECONDS) * 1000))


class LeaseStore:
    """Atomic lease operations over Redis sorted sets."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client
        self._acquire: AsyncScript = client.register_script(_ACQUIRE_SCRIPT)
        self._renew: AsyncScript = client.register_script(_RENEW_SCRIPT)
        self._release: AsyncScript = client.register_script(_RELEASE_SCRIPT)
        self._in_use: AsyncScript = client.register_script(_IN_USE_SCRIPT)

    @classmethod
    async def create(cls) -> Self:
        """Build a store on the process Redis client."""
        redis_client = await get_redis_client()
        return cls(await redis_client._get_client())

    def pool_exhausted(self) -> bool:
        """Whether every connection in the client's pool is checked out.

        redis-py 7.1's asyncio pool raises a plain ``ConnectionError`` when it
        is full, so callers check the pool rather than the error text. If the
        pool's internals change shape, this answers ``False`` (an outage, the
        old behaviour) instead of raising.
        """
        pool = self._client.connection_pool
        try:
            return len(pool._in_use_connections) >= pool.max_connections
        except (AttributeError, TypeError):
            return False

    async def try_acquire(
        self,
        org_id: OrganizationID,
        resource: ConcurrencyResource,
        lease_id: str,
        *,
        units: int,
        limit: int,
        ttl_seconds: float,
        waiter: Waiter | None = None,
    ) -> Admitted | Busy:
        """Take ``units`` if they fit under ``limit``; idempotent per lease ID.

        Args:
            org_id: Organization that owns the lease.
            resource: Resource the lease counts against.
            lease_id: Attempt-specific lease ID.
            units: Units to hold; must be between 1 and ``limit``.
            limit: The organization's concurrency limit.
            ttl_seconds: Seconds until the lease expires without renewal,
                capped at ``MAX_LEASE_TTL_SECONDS``.
            waiter: The run to queue for a wake if this is refused. A run is
                queued once; it keeps its place until a release pops it.

        Returns:
            ``Admitted`` or ``Busy``, each with the count in use.
        """
        result = await self._acquire(
            keys=[lease_key(org_id, resource), waiter_key(org_id, resource)],
            args=[
                lease_id,
                units,
                limit,
                _ttl_ms(ttl_seconds),
                _KEY_TTL_MS,
                waiter.member() if waiter is not None else "",
            ],
        )
        admitted, in_use = int(result[0]), int(result[1])
        if admitted:
            return Admitted(in_use=in_use, limit=limit)
        return Busy(in_use=in_use, limit=limit)

    async def renew(
        self,
        org_id: OrganizationID,
        resource: ConcurrencyResource,
        lease_id: str,
        *,
        units: int,
        ttl_seconds: float,
    ) -> bool:
        """Extend a held lease. Returns ``False`` if it had already expired."""
        result = await self._renew(
            keys=[lease_key(org_id, resource)],
            args=[lease_id, units, _ttl_ms(ttl_seconds), _KEY_TTL_MS],
        )
        return bool(result)

    async def release(
        self,
        org_id: OrganizationID,
        resource: ConcurrencyResource,
        lease_id: str,
        *,
        units: int,
    ) -> None:
        """Remove a lease. Releasing a missing lease is a no-op."""
        await self._client.zrem(lease_key(org_id, resource), *_members(lease_id, units))

    async def release_and_pop_waiter(
        self,
        org_id: OrganizationID,
        resource: ConcurrencyResource,
        lease_id: str,
        *,
        units: int,
    ) -> Waiter | None:
        """Remove a lease and, if that freed units, pop the oldest waiter.

        Returns:
            The run to wake, or ``None`` if nothing was freed or nobody waits.
        """
        member = await self._release(
            keys=[lease_key(org_id, resource), waiter_key(org_id, resource)],
            args=_members(lease_id, units),
        )
        return Waiter.from_member(member) if member else None

    async def pop_waiter(
        self, org_id: OrganizationID, resource: ConcurrencyResource
    ) -> Waiter | None:
        """Pop the oldest waiter, for a wake whose run had already closed."""
        popped = await self._client.zpopmin(waiter_key(org_id, resource), 1)
        if not popped:
            return None
        member, _score = popped[0]
        return Waiter.from_member(member)

    async def in_use(
        self, org_id: OrganizationID, resource: ConcurrencyResource
    ) -> int:
        """Count unexpired units held for an organization and resource."""
        result = await self._in_use(keys=[lease_key(org_id, resource)], args=[])
        return int(result)
