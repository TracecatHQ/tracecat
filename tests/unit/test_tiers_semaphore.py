from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as redis

from tracecat.tiers.activities import (
    TierActivities,
    acquire_action_permit_activity,
    heartbeat_action_permit_activity,
    release_action_permit_activity,
)
from tracecat.tiers.semaphore import RedisSemaphore

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000123")

# Callable returned by register_script: takes keys= and args=, returns awaitable.
_ScriptCallable = Callable[..., Awaitable[list[int] | int]]


class _FakeRedisClient:
    def __init__(
        self,
        *,
        acquire_result: list[int] | None = None,
        heartbeat_result: int = 1,
    ) -> None:
        self._acquire_result = acquire_result or [1, 1]
        self._heartbeat_result = heartbeat_result
        self.acquire_calls: list[tuple[list[str], list[str | int]]] = []
        self.heartbeat_calls: list[tuple[list[str], list[str | int]]] = []
        self.zrem = AsyncMock(return_value=1)
        self.zremrangebyscore = AsyncMock(return_value=0)
        self.zcard = AsyncMock(return_value=0)

    def register_script(self, script: str) -> _ScriptCallable:
        """Return a callable that mimics AsyncScript for acquire or heartbeat."""
        if "ZREMRANGEBYSCORE" in script:
            return self._acquire_script
        return self._heartbeat_script

    async def _acquire_script(
        self, *, keys: list[str], args: list[str | int]
    ) -> list[int]:
        self.acquire_calls.append((keys, args))
        return self._acquire_result

    async def _heartbeat_script(self, *, keys: list[str], args: list[str | int]) -> int:
        self.heartbeat_calls.append((keys, args))
        return self._heartbeat_result


@pytest.mark.anyio
async def test_semaphore_uses_distinct_scope_keys_for_acquire() -> None:
    client = _FakeRedisClient()
    semaphore = RedisSemaphore(cast(redis.Redis, client))

    await semaphore.acquire_workflow(
        org_id=ORG_ID,
        workflow_id="wf-123",
        limit=3,
    )
    await semaphore.acquire_action(
        org_id=ORG_ID,
        action_id="wf-123:root:step",
        limit=5,
    )

    assert client.acquire_calls[0][0] == [f"tier:org:{ORG_ID}:workflow-semaphore"]
    assert client.acquire_calls[1][0] == [f"tier:org:{ORG_ID}:action-semaphore"]


@pytest.mark.anyio
async def test_semaphore_uses_distinct_scope_keys_for_release_and_heartbeat() -> None:
    client = _FakeRedisClient()
    semaphore = RedisSemaphore(cast(redis.Redis, client))

    await semaphore.release_workflow(
        org_id=ORG_ID,
        workflow_id="wf-123",
    )
    await semaphore.release_action(
        org_id=ORG_ID,
        action_id="wf-123:root:step",
    )
    await semaphore.heartbeat_workflow(
        org_id=ORG_ID,
        workflow_id="wf-123",
    )
    await semaphore.heartbeat_action(
        org_id=ORG_ID,
        action_id="wf-123:root:step",
    )

    assert client.zrem.await_args_list[0].args == (
        f"tier:org:{ORG_ID}:workflow-semaphore",
        "wf-123",
    )
    assert client.zrem.await_args_list[1].args == (
        f"tier:org:{ORG_ID}:action-semaphore",
        "wf-123:root:step",
    )
    assert client.heartbeat_calls[0][0] == [f"tier:org:{ORG_ID}:workflow-semaphore"]
    assert client.heartbeat_calls[1][0] == [f"tier:org:{ORG_ID}:action-semaphore"]


@pytest.mark.anyio
async def test_heartbeat_returns_true_when_permit_exists() -> None:
    client = _FakeRedisClient(heartbeat_result=1)
    semaphore = RedisSemaphore(cast(redis.Redis, client))

    refreshed = await semaphore.heartbeat_workflow(org_id=ORG_ID, workflow_id="wf-123")

    assert refreshed is True


@pytest.mark.anyio
async def test_heartbeat_returns_false_when_permit_missing() -> None:
    client = _FakeRedisClient(heartbeat_result=0)
    semaphore = RedisSemaphore(cast(redis.Redis, client))

    refreshed = await semaphore.heartbeat_workflow(org_id=ORG_ID, workflow_id="wf-123")

    assert refreshed is False


@pytest.mark.anyio
async def test_release_is_idempotent_when_permit_missing() -> None:
    client = _FakeRedisClient()
    client.zrem = AsyncMock(return_value=0)
    semaphore = RedisSemaphore(cast(redis.Redis, client))

    await semaphore.release_workflow(org_id=ORG_ID, workflow_id="wf-123")
    await semaphore.release_workflow(org_id=ORG_ID, workflow_id="wf-123")

    assert client.zrem.await_count == 2
    assert client.zrem.await_args_list[0].args == (
        f"tier:org:{ORG_ID}:workflow-semaphore",
        "wf-123",
    )
    assert client.zrem.await_args_list[1].args == (
        f"tier:org:{ORG_ID}:workflow-semaphore",
        "wf-123",
    )


def test_tier_activities_include_action_permit_activities() -> None:
    activities = TierActivities.get_activities()
    assert acquire_action_permit_activity in activities
    assert release_action_permit_activity in activities
    assert heartbeat_action_permit_activity in activities
