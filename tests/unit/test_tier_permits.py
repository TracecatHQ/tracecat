from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.tiers.exceptions import InvalidOrganizationConcurrencyCapError
from tracecat.tiers.permits import TierPermitService
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.tiers.semaphore import AcquireResult, RedisSemaphore

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
async def test_get_effective_limits_for_org_uses_cache() -> None:
    permit_svc = TierPermitService(cast(RedisSemaphore, SimpleNamespace()))
    expected_limits = _limits(max_concurrent_actions=2)

    @asynccontextmanager
    async def _unexpected_session_context() -> AsyncIterator[object]:
        raise AssertionError("DB lookup should not run when cache has limits")
        yield

    with (
        patch(
            "tracecat.tiers.permits.get_effective_limits_cached",
            new=AsyncMock(return_value=expected_limits),
        ),
        patch(
            "tracecat.tiers.permits.set_effective_limits_cached",
            new=AsyncMock(),
        ),
        patch(
            "tracecat.tiers.permits.get_async_session_context_manager",
            new=_unexpected_session_context,
        ),
    ):
        limits, source = await permit_svc.get_effective_limits_for_org(ORG_ID)

    assert limits == expected_limits
    assert source == "cache"


@pytest.mark.anyio
async def test_get_effective_limits_for_org_populates_cache_on_miss() -> None:
    permit_svc = TierPermitService(cast(RedisSemaphore, SimpleNamespace()))
    expected_limits = _limits(max_concurrent_workflows=3)
    tier_service = SimpleNamespace(
        get_effective_limits=AsyncMock(return_value=expected_limits)
    )

    @asynccontextmanager
    async def _session_context() -> AsyncIterator[object]:
        yield object()

    with (
        patch(
            "tracecat.tiers.permits.get_effective_limits_cached",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat.tiers.permits.set_effective_limits_cached",
            new=AsyncMock(),
        ) as set_cache_mock,
        patch(
            "tracecat.tiers.permits.get_async_session_context_manager",
            new=_session_context,
        ),
        patch("tracecat.tiers.permits.TierService", return_value=tier_service),
    ):
        limits, source = await permit_svc.get_effective_limits_for_org(ORG_ID)

    assert limits == expected_limits
    assert source == "db"
    tier_service.get_effective_limits.assert_awaited_once_with(ORG_ID)
    set_cache_mock.assert_awaited_once_with(ORG_ID, expected_limits)


@pytest.mark.anyio
async def test_acquire_action_permit_uses_effective_limit() -> None:
    semaphore = SimpleNamespace(
        acquire_action=AsyncMock(
            return_value=AcquireResult(acquired=True, current_count=1)
        )
    )
    permit_svc = TierPermitService(cast(RedisSemaphore, semaphore))

    @asynccontextmanager
    async def _unexpected_session_context() -> AsyncIterator[object]:
        raise AssertionError("DB lookup should not run when cache has limits")
        yield

    with (
        patch(
            "tracecat.tiers.permits.get_effective_limits_cached",
            new=AsyncMock(return_value=_limits(max_concurrent_actions=2)),
        ),
        patch(
            "tracecat.tiers.permits.set_effective_limits_cached",
            new=AsyncMock(),
        ),
        patch(
            "tracecat.tiers.permits.get_async_session_context_manager",
            new=_unexpected_session_context,
        ),
    ):
        outcome = await permit_svc.acquire_action_permit(
            org_id=ORG_ID,
            action_id="wf:root:task",
        )

    assert outcome.result.acquired is True
    assert outcome.effective_limit == 2
    assert outcome.cap_source == "cache"
    semaphore.acquire_action.assert_awaited_once_with(
        org_id=ORG_ID,
        action_id="wf:root:task",
        limit=2,
    )


def test_normalize_concurrency_limit_rejects_non_positive_cap() -> None:
    with pytest.raises(InvalidOrganizationConcurrencyCapError):
        TierPermitService.normalize_concurrency_limit(
            limit=0,
            org_id=ORG_ID,
            scope="workflow",
        )
