"""Temporal activities for tier enforcement.

``get_tier_limits_activity`` stays registered so histories recorded before the
``dsl-admission-run-limits-v1`` patch still replay.
"""

from __future__ import annotations

from pydantic import BaseModel
from temporalio import activity

from tracecat.db.engine import get_async_session_tenant_context_manager
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
)
from tracecat.temporal.errors import raise_application_error_from_classification
from tracecat.tiers.exceptions import (
    DefaultTierNotConfiguredError,
)
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.tiers.service import TierService


class GetTierLimitsInput(BaseModel):
    """Input for getting tier limits."""

    org_id: OrganizationID
    """Organization ID."""


@activity.defn
async def get_tier_limits_activity(
    input: GetTierLimitsInput,
) -> EffectiveLimits:
    """Fetch tier limits for an organization.

    Args:
        input: Contains org_id.

    Returns:
        EffectiveLimits with the organization's effective limits.
    """
    try:
        # Activities run without a ctx_role; scope the read to the org explicitly.
        async with get_async_session_tenant_context_manager(
            organization_id=input.org_id
        ) as session:
            limits = await TierService(session).get_effective_limits(input.org_id)
    except Exception as e:
        invalid_data = isinstance(e, DefaultTierNotConfiguredError)
        classification = RuntimeErrorClassification.platform(
            kind=(
                RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA
                if invalid_data
                else RuntimeErrorKind.WORKFLOW_BOOTSTRAP_UNAVAILABLE
            ),
            message="Tracecat could not resolve workflow tier limits",
            retry_disposition=(
                RetryDisposition.NON_RETRYABLE
                if invalid_data
                else RetryDisposition.RETRYABLE
            ),
            cause=e,
        )
        raise_application_error_from_classification(classification)

    logger.debug(
        "Fetched tier limits",
        org_id=input.org_id,
        limits=limits.model_dump(),
    )

    return limits


class TierActivities:
    """Container for tier-related activities."""

    @staticmethod
    def get_activities() -> list:
        """Get all tier-related activities for worker registration."""
        return [get_tier_limits_activity]
