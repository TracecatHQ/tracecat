"""Redis-based semaphore for per-organization workflow concurrency control."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import redis.asyncio as redis

from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.identifiers import OrganizationID

# Default TTL for semaphore entries (1 hour)
DEFAULT_TTL_SECONDS = 3600


@dataclass(frozen=True)
class AcquireResult:
    """Result of attempting to acquire a workflow permit."""

    acquired: bool
    """Whether the permit was acquired."""
    current_count: int
    """Current number of workflows holding permits."""


# Lua script for atomic acquire operation
# KEYS[1] = semaphore key
# ARGV[1] = workflow_id, ARGV[2] = limit, ARGV[3] = now (unix timestamp), ARGV[4] = ttl
_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local wf_id = ARGV[1]
local limit = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

-- Prune stale entries (older than TTL)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - ttl)

-- Check if already holding (idempotent acquire)
if redis.call('ZSCORE', key, wf_id) then
    redis.call('ZADD', key, now, wf_id)  -- refresh timestamp
    return {1, redis.call('ZCARD', key)}  -- acquired=true
end

-- Check count
local count = redis.call('ZCARD', key)
if count >= limit then
    return {0, count}  -- acquired=false
end

-- Add and return
redis.call('ZADD', key, now, wf_id)
return {1, count + 1}  -- acquired=true
"""


class RedisSemaphore:
    """Per-organization workflow semaphore using Redis sorted sets.

    Uses sorted sets with Unix timestamps as scores for TTL-based pruning.
    Members are workflow execution IDs.
    """

    def __init__(self, client: redis.Redis, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        """Initialize the semaphore.

        Args:
            client: Redis client instance.
            ttl_seconds: TTL for semaphore entries in seconds.
        """
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._acquire_script: Any = None

    def _semaphore_key(self, org_id: OrganizationID) -> str:
        """Get the Redis key for an organization's semaphore."""
        return f"tier:org:{org_id}:semaphore"

    async def _get_acquire_script(self) -> Any:
        """Get or create the acquire Lua script."""
        if self._acquire_script is None:
            self._acquire_script = self._client.register_script(_ACQUIRE_SCRIPT)
        return self._acquire_script

    async def acquire(
        self,
        org_id: OrganizationID,
        workflow_id: str,
        limit: int,
    ) -> AcquireResult:
        """Try to acquire a permit for a workflow.

        Atomically:
        1. Prunes stale entries (older than TTL)
        2. Checks if workflow already holds a permit (idempotent)
        3. Checks current count against limit
        4. Adds workflow_id if under limit

        Args:
            org_id: Organization ID.
            workflow_id: Workflow execution ID.
            limit: Maximum concurrent workflows allowed.

        Returns:
            AcquireResult with acquired status and current count.
        """
        key = self._semaphore_key(org_id)
        now = int(time.time())

        script = await self._get_acquire_script()
        result = await script(
            keys=[key],
            args=[workflow_id, limit, now, self._ttl_seconds],
        )

        acquired = bool(result[0])
        current_count = int(result[1])

        logger.debug(
            "Semaphore acquire attempt",
            org_id=str(org_id),
            workflow_id=workflow_id,
            limit=limit,
            acquired=acquired,
            current_count=current_count,
        )

        return AcquireResult(acquired=acquired, current_count=current_count)

    async def release(self, org_id: OrganizationID, workflow_id: str) -> None:
        """Release a permit for a workflow.

        Args:
            org_id: Organization ID.
            workflow_id: Workflow execution ID.
        """
        key = self._semaphore_key(org_id)
        removed = await self._client.zrem(key, workflow_id)

        logger.debug(
            "Semaphore release",
            org_id=str(org_id),
            workflow_id=workflow_id,
            removed=bool(removed),
        )

    async def heartbeat(self, org_id: OrganizationID, workflow_id: str) -> bool:
        """Refresh TTL for a long-running workflow.

        Updates the timestamp for the workflow's entry, preventing it from
        being pruned as stale.

        Args:
            org_id: Organization ID.
            workflow_id: Workflow execution ID.

        Returns:
            True if the workflow was found and updated, False otherwise.
        """
        key = self._semaphore_key(org_id)
        now = int(time.time())

        # Only update if the entry exists (XX flag)
        result = await self._client.zadd(key, {workflow_id: now}, xx=True)

        logger.debug(
            "Semaphore heartbeat",
            org_id=str(org_id),
            workflow_id=workflow_id,
            updated=bool(result),
        )

        return bool(result)

    async def get_count(self, org_id: OrganizationID) -> int:
        """Get the current count of workflows holding permits.

        Note: This performs TTL pruning before counting.

        Args:
            org_id: Organization ID.

        Returns:
            Current count of active workflows.
        """
        key = self._semaphore_key(org_id)
        now = int(time.time())

        # Prune stale entries first
        await self._client.zremrangebyscore(key, "-inf", now - self._ttl_seconds)

        return await self._client.zcard(key)
