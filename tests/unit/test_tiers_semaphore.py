from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from tracecat.tiers.activities import (
    TierActivities,
    acquire_action_permit_activity,
    heartbeat_action_permit_activity,
    release_action_permit_activity,
)
from tracecat.tiers.semaphore import RedisSemaphore


class _ScriptRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], list[object]]] = []

    async def __call__(self, *, keys: list[str], args: list[object]) -> list[int]:
        self.calls.append((keys, args))
        return [1, 1]


class _FakeRedisClient:
    def __init__(self, script: _ScriptRecorder) -> None:
        self._script = script
        self.zrem = AsyncMock(return_value=1)
        self.zadd = AsyncMock(return_value=1)
        self.zremrangebyscore = AsyncMock(return_value=0)
        self.zcard = AsyncMock(return_value=0)

    def register_script(self, _: str) -> Callable[..., object]:
        return self._script


@pytest.mark.anyio
async def test_semaphore_uses_distinct_scope_keys_for_acquire() -> None:
    script = _ScriptRecorder()
    client = _FakeRedisClient(script)
    semaphore = RedisSemaphore(client)  # type: ignore[arg-type]

    await semaphore.acquire_workflow(
        org_id="org-123",  # type: ignore[arg-type]
        workflow_id="wf-123",
        limit=3,
    )
    await semaphore.acquire_action(
        org_id="org-123",  # type: ignore[arg-type]
        action_id="wf-123:root:step",
        limit=5,
    )

    assert script.calls[0][0] == ["tier:org:org-123:semaphore"]
    assert script.calls[1][0] == ["tier:org:org-123:action-semaphore"]


@pytest.mark.anyio
async def test_semaphore_uses_distinct_scope_keys_for_release_and_heartbeat() -> None:
    script = _ScriptRecorder()
    client = _FakeRedisClient(script)
    semaphore = RedisSemaphore(client)  # type: ignore[arg-type]

    await semaphore.release_workflow(
        org_id="org-123",  # type: ignore[arg-type]
        workflow_id="wf-123",
    )
    await semaphore.release_action(
        org_id="org-123",  # type: ignore[arg-type]
        action_id="wf-123:root:step",
    )
    await semaphore.heartbeat_workflow(
        org_id="org-123",  # type: ignore[arg-type]
        workflow_id="wf-123",
    )
    await semaphore.heartbeat_action(
        org_id="org-123",  # type: ignore[arg-type]
        action_id="wf-123:root:step",
    )

    assert client.zrem.await_args_list[0].args == ("tier:org:org-123:semaphore", "wf-123")
    assert client.zrem.await_args_list[1].args == (
        "tier:org:org-123:action-semaphore",
        "wf-123:root:step",
    )
    assert client.zadd.await_args_list[0].args[0] == "tier:org:org-123:semaphore"
    assert client.zadd.await_args_list[1].args[0] == "tier:org:org-123:action-semaphore"


def test_tier_activities_include_action_permit_activities() -> None:
    activities = TierActivities.get_activities()
    assert acquire_action_permit_activity in activities
    assert release_action_permit_activity in activities
    assert heartbeat_action_permit_activity in activities
