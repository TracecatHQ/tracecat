from __future__ import annotations

import uuid
from types import SimpleNamespace
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
    AcquireActionPermitInput,
    AcquireWorkflowPermitInput,
    GetTierLimitsInput,
    acquire_action_permit_activity,
    acquire_workflow_permit_activity,
    get_tier_limits_activity,
)
from tracecat.tiers.exceptions import (
    DefaultTierNotConfiguredError,
    InvalidOrganizationConcurrencyCapError,
)
from tracecat.tiers.permits import PermitAcquireOutcome
from tracecat.tiers.semaphore import AcquireResult

ORG_ID = uuid.UUID("00000000-0000-4000-8000-000000000111")


@pytest.mark.anyio
async def test_acquire_action_permit_activity_uses_permit_service() -> None:
    permit_svc = SimpleNamespace(
        acquire_action_permit=AsyncMock(
            return_value=PermitAcquireOutcome(
                result=AcquireResult(acquired=True, current_count=1),
                effective_limit=2,
                cap_source="cache",
            )
        )
    )

    with patch(
        "tracecat.tiers.activities.TierPermitService.create",
        new=AsyncMock(return_value=permit_svc),
    ):
        result = await acquire_action_permit_activity(
            AcquireActionPermitInput(org_id=ORG_ID, action_id="wf:root:task", limit=99)
        )

    assert result.acquired is True
    permit_svc.acquire_action_permit.assert_awaited_once_with(
        org_id=ORG_ID,
        action_id="wf:root:task",
    )


@pytest.mark.anyio
async def test_acquire_workflow_permit_activity_maps_invalid_cap_error() -> None:
    permit_svc = SimpleNamespace(
        acquire_workflow_permit=AsyncMock(
            side_effect=InvalidOrganizationConcurrencyCapError(
                scope="workflow",
                org_id=ORG_ID,
                limit=0,
            )
        )
    )

    with patch(
        "tracecat.tiers.activities.TierPermitService.create",
        new=AsyncMock(return_value=permit_svc),
    ):
        with pytest.raises(ApplicationError) as exc_info:
            await acquire_workflow_permit_activity(
                AcquireWorkflowPermitInput(
                    org_id=ORG_ID,
                    workflow_id="wf-exec",
                    limit=99,
                )
            )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert str(ORG_ID) not in str(exc_info.value)


@pytest.mark.anyio
async def test_acquire_action_permit_activity_classifies_service_failure() -> None:
    diagnostic = "permit diagnostic must not enter history"
    with patch(
        "tracecat.tiers.activities.TierPermitService.create",
        new=AsyncMock(side_effect=RuntimeError(diagnostic)),
    ):
        with pytest.raises(ApplicationError) as exc_info:
            await acquire_action_permit_activity(
                AcquireActionPermitInput(
                    org_id=ORG_ID,
                    action_id="wf:root:task",
                    limit=99,
                )
            )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.WORKFLOW_BOOTSTRAP_UNAVAILABLE
    assert classification.retry_disposition is RetryDisposition.RETRYABLE
    assert diagnostic not in str(exc_info.value)


@pytest.mark.anyio
async def test_get_tier_limits_activity_classifies_service_failure() -> None:
    diagnostic = "tier diagnostic must not enter history"
    mock_service = AsyncMock()
    mock_service.get_effective_limits.side_effect = RuntimeError(diagnostic)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_service

    with patch(
        "tracecat.tiers.activities.TierService.with_session",
        return_value=mock_ctx,
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
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_service

    with patch(
        "tracecat.tiers.activities.TierService.with_session",
        return_value=mock_ctx,
    ):
        with pytest.raises(ApplicationError) as exc_info:
            await get_tier_limits_activity(GetTierLimitsInput(org_id=ORG_ID))

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
    assert "Run database migrations" not in str(exc_info.value)
