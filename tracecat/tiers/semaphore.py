"""Redis-based semaphore for per-organization workflow concurrency control."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import redis.asyncio as redis

from tracecat.logger import logger

if TYPE_CHECKING:
    from redis.commands.core import AsyncScript

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


class PermitScope(StrEnum):
    WORKFLOW = "workflow"
    ACTION = "action"


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

# Lua script for heartbeat updates on existing permits only
# KEYS[1] = semaphore key
# ARGV[1] = permit_id, ARGV[2] = now (unix timestamp)
_HEARTBEAT_SCRIPT = """
local key = KEYS[1]
local permit_id = ARGV[1]
local now = tonumber(ARGV[2])

if not redis.call('ZSCORE', key, permit_id) then
    return 0
end

redis.call('ZADD', key, now, permit_id)
return 1
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
        self._acquire_script: AsyncScript | None = None
        self._heartbeat_script: AsyncScript | None = None

    def _semaphore_key(self, org_id: OrganizationID, scope: PermitScope) -> str:
        """Get the Redis key for an organization's semaphore."""
        if scope == PermitScope.WORKFLOW:
            # Keep workflow key stable for backward compatibility.
            return f"tier:org:{org_id}:semaphore"
        return f"tier:org:{org_id}:action-semaphore"

    async def _get_acquire_script(self) -> AsyncScript:
        """Get or create the acquire Lua script."""
        if self._acquire_script is None:
            self._acquire_script = self._client.register_script(_ACQUIRE_SCRIPT)
        return self._acquire_script

    async def _get_heartbeat_script(self) -> AsyncScript:
        """Get or create the heartbeat Lua script."""
        if self._heartbeat_script is None:
            self._heartbeat_script = self._client.register_script(_HEARTBEAT_SCRIPT)
        return self._heartbeat_script

    async def _acquire(
        self,
        org_id: OrganizationID,
        permit_id: str,
        limit: int,
        *,
        scope: PermitScope,
    ) -> AcquireResult:
        """Try to acquire a permit.

        Atomically:
        1. Prunes stale entries (older than TTL)
        2. Checks if permit holder already holds a permit (idempotent)
        3. Checks current count against limit
        4. Adds permit_id if under limit

        Args:
            org_id: Organization ID.
            permit_id: Permit holder ID.
            limit: Maximum concurrent entries allowed.
            scope: Permit scope namespace.

        Returns:
            AcquireResult with acquired status and current count.
        """
        key = self._semaphore_key(org_id, scope)
        now = int(time.time())

        script = await self._get_acquire_script()
        result = await script(
            keys=[key],
            args=[permit_id, limit, now, self._ttl_seconds],
        )

        acquired = bool(result[0])
        current_count = int(result[1])

        logger.debug(
            "Semaphore acquire attempt",
            org_id=str(org_id),
            permit_id=permit_id,
            scope=scope,
            limit=limit,
            acquired=acquired,
            current_count=current_count,
        )

        return AcquireResult(acquired=acquired, current_count=current_count)

    async def _release(
        self,
        org_id: OrganizationID,
        permit_id: str,
        *,
        scope: PermitScope,
    ) -> None:
        """Release a permit.

        Args:
            org_id: Organization ID.
            permit_id: Permit holder ID.
            scope: Permit scope namespace.
        """
        key = self._semaphore_key(org_id, scope)
        removed = await self._client.zrem(key, permit_id)

        logger.debug(
            "Semaphore release",
            org_id=str(org_id),
            permit_id=permit_id,
            scope=scope,
            removed=bool(removed),
        )

    async def _heartbeat(
        self,
        org_id: OrganizationID,
        permit_id: str,
        *,
        scope: PermitScope,
    ) -> bool:
        """Refresh TTL for a long-running permit holder.

        Updates the timestamp for the permit's entry, preventing it from
        being pruned as stale.

        Args:
            org_id: Organization ID.
            permit_id: Permit holder ID.
            scope: Permit scope namespace.

        Returns:
            True if the permit was found and updated, False otherwise.
        """
        key = self._semaphore_key(org_id, scope)
        now = int(time.time())

        script = await self._get_heartbeat_script()
        result = await script(keys=[key], args=[permit_id, now])
        updated = bool(result)

        logger.debug(
            "Semaphore heartbeat",
            org_id=str(org_id),
            permit_id=permit_id,
            scope=scope,
            updated=updated,
        )

        return updated

    async def get_count(self, org_id: OrganizationID, scope: PermitScope) -> int:
        """Get the current count of permit holders.

        Note: This performs TTL pruning before counting.

        Args:
            org_id: Organization ID.
            scope: Permit scope namespace.

        Returns:
            Current count of active workflows.
        """
        key = self._semaphore_key(org_id, scope)
        now = int(time.time())

        # Prune stale entries first
        await self._client.zremrangebyscore(key, "-inf", now - self._ttl_seconds)

        return await self._client.zcard(key)

    async def acquire_workflow(
        self,
        org_id: OrganizationID,
        workflow_id: str,
        limit: int,
    ) -> AcquireResult:
        """Acquire a workflow permit."""
        return await self._acquire(
            org_id=org_id,
            permit_id=workflow_id,
            limit=limit,
            scope=PermitScope.WORKFLOW,
        )

    async def release_workflow(self, org_id: OrganizationID, workflow_id: str) -> None:
        """Release a workflow permit."""
        await self._release(
            org_id=org_id,
            permit_id=workflow_id,
            scope=PermitScope.WORKFLOW,
        )

    async def heartbeat_workflow(
        self, org_id: OrganizationID, workflow_id: str
    ) -> bool:
        """Heartbeat a workflow permit."""
        return await self._heartbeat(
            org_id=org_id,
            permit_id=workflow_id,
            scope=PermitScope.WORKFLOW,
        )

    async def acquire_action(
        self,
        org_id: OrganizationID,
        action_id: str,
        limit: int,
    ) -> AcquireResult:
        """Acquire an action permit."""
        return await self._acquire(
            org_id=org_id,
            permit_id=action_id,
            limit=limit,
            scope=PermitScope.ACTION,
        )

    async def release_action(self, org_id: OrganizationID, action_id: str) -> None:
        """Release an action permit."""
        await self._release(
            org_id=org_id,
            permit_id=action_id,
            scope=PermitScope.ACTION,
        )

    async def heartbeat_action(self, org_id: OrganizationID, action_id: str) -> bool:
        """Heartbeat an action permit."""
        return await self._heartbeat(
            org_id=org_id,
            permit_id=action_id,
            scope=PermitScope.ACTION,
        )
