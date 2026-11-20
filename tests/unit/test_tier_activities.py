from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import extract_error_classification
from tracecat.tiers.activities import (
    GetTierLimitsInput,
    get_tier_limits_activity,
)
from tracecat.tiers.exceptions import DefaultTierNotConfiguredError
from tracecat.tiers.schemas import EffectiveLimits

ORG_ID = uuid.UUID("00000000-0000-4000-8000-000000000111")


@pytest.mark.anyio
async def test_get_tier_limits_activity_classifies_service_failure() -> None:
    diagnostic = "tier diagnostic must not enter history"
    mock_service = AsyncMock()
    mock_service.get_effective_limits.side_effect = RuntimeError(diagnostic)
    with (
        patch(
            "tracecat.tiers.activities.get_async_session_tenant_context_manager",
            return_value=AsyncMock(),
        ),
        patch("tracecat.tiers.activities.TierService", return_value=mock_service),
    ):
        with pytest.raises(ApplicationError) as exc_info:
            await get_tier_limits_activity(GetTierLimitsInput(org_id=ORG_ID))

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.WORKFLOW_BOOTSTRAP_UNAVAILABLE
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
    assert diagnostic not in str(exc_info.value)


@pytest.mark.anyio
async def test_get_tier_limits_activity_maps_missing_default_tier() -> None:
    mock_service = AsyncMock()
    mock_service.get_effective_limits.side_effect = DefaultTierNotConfiguredError()
    with (
        patch(
            "tracecat.tiers.activities.get_async_session_tenant_context_manager",
            return_value=AsyncMock(),
        ),
        patch("tracecat.tiers.activities.TierService", return_value=mock_service),
    ):
        with pytest.raises(ApplicationError) as exc_info:
            await get_tier_limits_activity(GetTierLimitsInput(org_id=ORG_ID))

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert "Run database migrations" not in str(exc_info.value)


@pytest.mark.anyio
async def test_get_tier_limits_activity_reads_tier_in_org_scope() -> None:
    """The activity has no ctx_role; a role-derived session would see no org tier."""
    limits = EffectiveLimits(
        api_rate_limit=None,
        api_burst_capacity=None,
        max_concurrent_workflows=3,
        max_action_executions_per_workflow=None,
        max_concurrent_actions=None,
    )
    mock_service = AsyncMock()
    mock_service.get_effective_limits.return_value = limits

    with (
        patch(
            "tracecat.tiers.activities.get_async_session_tenant_context_manager",
            return_value=AsyncMock(),
        ) as tenant_session,
        patch("tracecat.tiers.activities.TierService", return_value=mock_service),
    ):
        result = await get_tier_limits_activity(GetTierLimitsInput(org_id=ORG_ID))

    assert result == limits
    tenant_session.assert_called_once_with(organization_id=ORG_ID)
    mock_service.get_effective_limits.assert_awaited_once_with(ORG_ID)
