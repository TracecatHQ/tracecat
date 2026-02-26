"""Temporal activities for tier enforcement."""

from __future__ import annotations

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.db.engine import get_async_session_context_manager
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.tiers.limits_cache import (
    get_effective_limits_cached,
    set_effective_limits_cached,
)
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.tiers.semaphore import AcquireResult, RedisSemaphore
from tracecat.tiers.service import TierService

_UNBOUNDED_CONCURRENCY_LIMIT = 2_147_483_647


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
    limits, cap_source = await _get_effective_limits_for_org(input.org_id)
    effective_limit = _normalize_concurrency_limit(
        limit=limits.max_concurrent_workflows,
        org_id=input.org_id,
        scope="workflow",
    )

    redis_client = await get_redis_client()
    # Get the underlying redis.Redis client for the semaphore
    client = await redis_client._get_client()
    semaphore = RedisSemaphore(client)

    result = await semaphore.acquire_workflow(
        org_id=input.org_id,
        workflow_id=input.workflow_id,
        limit=effective_limit,
    )

    logger.info(
        "Workflow permit acquire attempt",
        org_id=input.org_id,
        workflow_id=input.workflow_id,
        requested_limit=input.limit,
        effective_limit=effective_limit,
        cap_source=cap_source,
        acquired=result.acquired,
        current_count=result.current_count,
    )

    return result


@activity.defn
async def release_workflow_permit_activity(
    input: ReleaseWorkflowPermitInput,
) -> None:
    """Release a workflow execution permit.

    Args:
        input: Contains org_id and workflow_id.
    """
    redis_client = await get_redis_client()
    client = await redis_client._get_client()
    semaphore = RedisSemaphore(client)

    await semaphore.release_workflow(
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
    semaphore = await _get_redis_semaphore()

    return await semaphore.heartbeat_workflow(
        org_id=input.org_id,
        workflow_id=input.workflow_id,
    )


@activity.defn
async def acquire_action_permit_activity(
    input: AcquireActionPermitInput,
) -> AcquireResult:
    """Try to acquire an action execution permit."""
    limits, cap_source = await _get_effective_limits_for_org(input.org_id)
    effective_limit = _normalize_concurrency_limit(
        limit=limits.max_concurrent_actions,
        org_id=input.org_id,
        scope="action",
    )

    semaphore = await _get_redis_semaphore()

    result = await semaphore.acquire_action(
        org_id=input.org_id,
        action_id=input.action_id,
        limit=effective_limit,
    )

    logger.info(
        "Action permit acquire attempt",
        org_id=input.org_id,
        action_id=input.action_id,
        requested_limit=input.limit,
        effective_limit=effective_limit,
        cap_source=cap_source,
        acquired=result.acquired,
        current_count=result.current_count,
    )
    return result


@activity.defn
async def release_action_permit_activity(
    input: ReleaseActionPermitInput,
) -> None:
    """Release an action execution permit."""
    semaphore = await _get_redis_semaphore()

    await semaphore.release_action(
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
    semaphore = await _get_redis_semaphore()

    return await semaphore.heartbeat_action(
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


async def _get_redis_semaphore() -> RedisSemaphore:
    """Return a configured Redis semaphore for this process."""
    redis_client = await get_redis_client()
    client = await redis_client._get_client()
    return RedisSemaphore(client)


def _normalize_concurrency_limit(
    *, limit: int | None, org_id: OrganizationID, scope: str
) -> int:
    if limit is None:
        return _UNBOUNDED_CONCURRENCY_LIMIT
    if limit <= 0:
        raise ApplicationError(
            (
                "Invalid organization concurrency cap: "
                f"scope={scope} org_id={org_id} limit={limit}"
            ),
            non_retryable=True,
            type="InvalidOrganizationConcurrencyCap",
        )
    return limit


async def _get_effective_limits_for_org(
    org_id: OrganizationID,
) -> tuple[EffectiveLimits, str]:
    try:
        limits = await get_effective_limits_cached(org_id)
    except Exception as e:
        logger.warning(
            "Failed to read effective limits cache",
            org_id=org_id,
            error=e,
        )
        limits = None
    if limits is not None:
        return limits, "cache"

    async with get_async_session_context_manager() as session:
        service = TierService(session)
        limits = await service.get_effective_limits(org_id)

    try:
        await set_effective_limits_cached(org_id, limits)
    except Exception as e:
        logger.warning(
            "Failed to update effective limits cache",
            org_id=org_id,
            error=e,
        )
    return limits, "db"


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
