from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.tiers.activities import (
    AcquireActionPermitInput,
    AcquireWorkflowPermitInput,
    acquire_action_permit_activity,
    acquire_workflow_permit_activity,
)
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.tiers.semaphore import AcquireResult

ORG_ID = uuid.UUID("00000000-0000-4000-8000-000000000111")


def _limits(
    *,
    max_concurrent_workflows: int | None = None,
    max_concurrent_actions: int | None = None,
) -> EffectiveLimits:
    return EffectiveLimits(
        api_rate_limit=None,
        api_burst_capacity=None,
        max_concurrent_workflows=max_concurrent_workflows,
        max_action_executions_per_workflow=None,
        max_concurrent_actions=max_concurrent_actions,
    )


@pytest.mark.anyio
async def test_acquire_action_permit_activity_uses_cached_effective_limit() -> None:
    semaphore = SimpleNamespace(
        acquire_action=AsyncMock(
            return_value=AcquireResult(acquired=True, current_count=1)
        )
    )
    redis_wrapper = SimpleNamespace(_get_client=AsyncMock(return_value=object()))

    @asynccontextmanager
    async def _unexpected_session_context() -> AsyncIterator[object]:
        raise AssertionError("DB lookup should not run when cache has limits")
        yield

    with (
        patch(
            "tracecat.tiers.activities.get_effective_limits_cached",
            new=AsyncMock(return_value=_limits(max_concurrent_actions=2)),
        ),
        patch(
            "tracecat.tiers.activities.set_effective_limits_cached",
            new=AsyncMock(),
        ),
        patch(
            "tracecat.tiers.activities.get_async_session_context_manager",
            new=_unexpected_session_context,
        ),
        patch("tracecat.tiers.activities.RedisSemaphore", return_value=semaphore),
        patch(
            "tracecat.tiers.activities.get_redis_client",
            new=AsyncMock(return_value=redis_wrapper),
        ),
    ):
        result = await acquire_action_permit_activity(
            AcquireActionPermitInput(org_id=ORG_ID, action_id="wf:root:task", limit=99)
        )

    assert result.acquired is True
    semaphore.acquire_action.assert_awaited_once()
    assert semaphore.acquire_action.await_args.kwargs["limit"] == 2


@pytest.mark.anyio
async def test_acquire_workflow_permit_activity_populates_cache_on_miss() -> None:
    expected_limits = _limits(max_concurrent_workflows=3)
    semaphore = SimpleNamespace(
        acquire_workflow=AsyncMock(
            return_value=AcquireResult(acquired=True, current_count=1)
        )
    )
    redis_wrapper = SimpleNamespace(_get_client=AsyncMock(return_value=object()))
    tier_service = SimpleNamespace(
        get_effective_limits=AsyncMock(return_value=expected_limits)
    )

    @asynccontextmanager
    async def _session_context() -> AsyncIterator[object]:
        yield object()

    with (
        patch(
            "tracecat.tiers.activities.get_effective_limits_cached",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat.tiers.activities.set_effective_limits_cached",
            new=AsyncMock(),
        ) as set_cache_mock,
        patch(
            "tracecat.tiers.activities.get_async_session_context_manager",
            new=_session_context,
        ),
        patch("tracecat.tiers.activities.TierService", return_value=tier_service),
        patch("tracecat.tiers.activities.RedisSemaphore", return_value=semaphore),
        patch(
            "tracecat.tiers.activities.get_redis_client",
            new=AsyncMock(return_value=redis_wrapper),
        ),
    ):
        result = await acquire_workflow_permit_activity(
            AcquireWorkflowPermitInput(org_id=ORG_ID, workflow_id="wf-exec", limit=99)
        )

    assert result.acquired is True
    tier_service.get_effective_limits.assert_awaited_once_with(ORG_ID)
    set_cache_mock.assert_awaited_once_with(ORG_ID, expected_limits)
    semaphore.acquire_workflow.assert_awaited_once()
    assert semaphore.acquire_workflow.await_args.kwargs["limit"] == 3


@pytest.mark.anyio
async def test_acquire_action_permit_activity_rejects_non_positive_cap() -> None:
    with patch(
        "tracecat.tiers.activities.get_effective_limits_cached",
        new=AsyncMock(return_value=_limits(max_concurrent_actions=0)),
    ):
        with pytest.raises(
            ApplicationError, match="Invalid organization concurrency cap"
        ):
            await acquire_action_permit_activity(
                AcquireActionPermitInput(
                    org_id=ORG_ID,
                    action_id="wf:root:task",
                    limit=1,
                )
            )
