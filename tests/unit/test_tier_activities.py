from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from tracecat import config
from tracecat.dsl import init_activities
from tracecat.dsl.init_activities import RunLimitsSnapshot, resolve_run_limits_activity
from tracecat.feature_flags.enums import FeatureFlag
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


@pytest.fixture
def limits_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS}
    )


@pytest.mark.anyio
async def test_resolve_run_limits_activity_skips_lookup_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", set())
    lookup = AsyncMock()
    with patch.object(init_activities, "get_effective_limits", new=lookup):
        snapshot = await resolve_run_limits_activity(ORG_ID)

    assert snapshot == RunLimitsSnapshot()
    lookup.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.usefixtures("limits_flag_on")
async def test_resolve_run_limits_activity_returns_the_action_budget() -> None:
    limits = EffectiveLimits(
        api_rate_limit=None,
        api_burst_capacity=None,
        max_concurrent_workflows=None,
        max_action_executions_per_workflow=12,
        max_concurrent_actions=3,
    )
    with patch.object(
        init_activities, "get_effective_limits", new=AsyncMock(return_value=limits)
    ):
        snapshot = await resolve_run_limits_activity(ORG_ID)

    assert snapshot == RunLimitsSnapshot(max_action_executions_per_workflow=12)


@pytest.mark.anyio
@pytest.mark.usefixtures("limits_flag_on")
@pytest.mark.parametrize(
    ("error", "kind", "disposition"),
    [
        (
            DefaultTierNotConfiguredError(),
            RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA,
            RetryDisposition.NON_RETRYABLE,
        ),
        (
            RuntimeError("db down"),
            RuntimeErrorKind.WORKFLOW_BOOTSTRAP_UNAVAILABLE,
            RetryDisposition.RETRYABLE,
        ),
    ],
)
async def test_resolve_run_limits_activity_classifies_lookup_failures(
    error: Exception, kind: RuntimeErrorKind, disposition: RetryDisposition
) -> None:
    with (
        patch.object(
            init_activities, "get_effective_limits", new=AsyncMock(side_effect=error)
        ),
        pytest.raises(ApplicationError) as exc_info,
    ):
        await resolve_run_limits_activity(ORG_ID)

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is kind
    assert classification.retry_disposition is disposition
