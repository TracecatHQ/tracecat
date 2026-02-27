"""Temporal activities for tier enforcement."""

from __future__ import annotations

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.tiers.exceptions import InvalidOrganizationConcurrencyCapError
from tracecat.tiers.permits import TierPermitService
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.tiers.semaphore import AcquireResult
from tracecat.tiers.service import TierService


class AcquireWorkflowPermitInput(BaseModel):
    """Input for acquiring a workflow execution permit."""

    org_id: OrganizationID
    """Organization ID."""
    workflow_id: str
    """Workflow execution ID."""
    limit: int
    """Requested concurrency limit (legacy advisory input)."""


class ReleaseWorkflowPermitInput(BaseModel):
    """Input for releasing a workflow execution permit."""

    org_id: OrganizationID
    """Organization ID."""
    workflow_id: str
    """Workflow execution ID."""


class HeartbeatWorkflowPermitInput(BaseModel):
    """Input for refreshing a workflow permit TTL."""

    org_id: OrganizationID
    """Organization ID."""
    workflow_id: str
    """Workflow execution ID."""


class GetTierLimitsInput(BaseModel):
    """Input for getting tier limits."""

    org_id: OrganizationID
    """Organization ID."""


class AcquireActionPermitInput(BaseModel):
    """Input for acquiring an action execution permit."""

    org_id: OrganizationID
    """Organization ID."""
    action_id: str
    """Action execution ID."""
    limit: int
    """Requested concurrency limit (legacy advisory input)."""


class ReleaseActionPermitInput(BaseModel):
    """Input for releasing an action execution permit."""

    org_id: OrganizationID
    """Organization ID."""
    action_id: str
    """Action execution ID."""


class HeartbeatActionPermitInput(BaseModel):
    """Input for refreshing an action execution permit TTL."""

    org_id: OrganizationID
    """Organization ID."""
    action_id: str
    """Action execution ID."""


@activity.defn
async def acquire_workflow_permit_activity(
    input: AcquireWorkflowPermitInput,
) -> AcquireResult:
    """Try to acquire a workflow execution permit.

    Uses Redis semaphore to enforce per-organization concurrent workflow limits.

    Returns:
        AcquireResult with acquired status and current count.
    """
    permit_svc = await TierPermitService.create()
    try:
        outcome = await permit_svc.acquire_workflow_permit(
            org_id=input.org_id,
            workflow_id=input.workflow_id,
        )
    except InvalidOrganizationConcurrencyCapError as e:
        raise ApplicationError(
            str(e),
            non_retryable=True,
            type="InvalidOrganizationConcurrencyCap",
        ) from e

    logger.info(
        "Workflow permit acquire attempt",
        org_id=input.org_id,
        workflow_id=input.workflow_id,
        requested_limit=input.limit,
        effective_limit=outcome.effective_limit,
        cap_source=outcome.cap_source,
        acquired=outcome.result.acquired,
        current_count=outcome.result.current_count,
    )

    return outcome.result


@activity.defn
async def release_workflow_permit_activity(
    input: ReleaseWorkflowPermitInput,
) -> None:
    """Release a workflow execution permit.

    Args:
        input: Contains org_id and workflow_id.
    """
    permit_svc = await TierPermitService.create()
    await permit_svc.release_workflow_permit(
        org_id=input.org_id,
        workflow_id=input.workflow_id,
    )

    logger.info(
        "Workflow permit released",
        org_id=input.org_id,
        workflow_id=input.workflow_id,
    )


@activity.defn
async def heartbeat_workflow_permit_activity(
    input: HeartbeatWorkflowPermitInput,
) -> bool:
    """Refresh TTL for a long-running workflow's permit.

    Args:
        input: Contains org_id and workflow_id.

    Returns:
        True if the permit was found and updated, False otherwise.
    """
    permit_svc = await TierPermitService.create()
    return await permit_svc.heartbeat_workflow_permit(
        org_id=input.org_id,
        workflow_id=input.workflow_id,
    )


@activity.defn
async def acquire_action_permit_activity(
    input: AcquireActionPermitInput,
) -> AcquireResult:
    """Try to acquire an action execution permit."""
    permit_svc = await TierPermitService.create()
    try:
        outcome = await permit_svc.acquire_action_permit(
            org_id=input.org_id,
            action_id=input.action_id,
        )
    except InvalidOrganizationConcurrencyCapError as e:
        raise ApplicationError(
            str(e),
            non_retryable=True,
            type="InvalidOrganizationConcurrencyCap",
        ) from e

    logger.info(
        "Action permit acquire attempt",
        org_id=input.org_id,
        action_id=input.action_id,
        requested_limit=input.limit,
        effective_limit=outcome.effective_limit,
        cap_source=outcome.cap_source,
        acquired=outcome.result.acquired,
        current_count=outcome.result.current_count,
    )
    return outcome.result


@activity.defn
async def release_action_permit_activity(
    input: ReleaseActionPermitInput,
) -> None:
    """Release an action execution permit."""
    permit_svc = await TierPermitService.create()
    await permit_svc.release_action_permit(
        org_id=input.org_id,
        action_id=input.action_id,
    )

    logger.info(
        "Action permit released",
        org_id=input.org_id,
        action_id=input.action_id,
    )


@activity.defn
async def heartbeat_action_permit_activity(
    input: HeartbeatActionPermitInput,
) -> bool:
    """Refresh TTL for an action execution permit."""
    permit_svc = await TierPermitService.create()
    return await permit_svc.heartbeat_action_permit(
        org_id=input.org_id,
        action_id=input.action_id,
    )


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
    async with TierService.with_session() as service:
        limits = await service.get_effective_limits(input.org_id)

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
        return [
            acquire_workflow_permit_activity,
            release_workflow_permit_activity,
            heartbeat_workflow_permit_activity,
            acquire_action_permit_activity,
            release_action_permit_activity,
            heartbeat_action_permit_activity,
            get_tier_limits_activity,
        ]
