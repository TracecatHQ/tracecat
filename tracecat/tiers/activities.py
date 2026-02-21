"""Temporal activities for tier enforcement."""

from __future__ import annotations

from pydantic import BaseModel
from temporalio import activity

from tracecat.db.engine import get_async_session_context_manager
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.tiers.semaphore import AcquireResult, RedisSemaphore
from tracecat.tiers.service import TierService


class AcquireWorkflowPermitInput(BaseModel):
    """Input for acquiring a workflow execution permit."""

    org_id: str
    """Organization ID (as string for serialization)."""
    workflow_id: str
    """Workflow execution ID."""
    limit: int
    """Maximum concurrent workflows allowed."""


class ReleaseWorkflowPermitInput(BaseModel):
    """Input for releasing a workflow execution permit."""

    org_id: str
    """Organization ID (as string for serialization)."""
    workflow_id: str
    """Workflow execution ID."""


class HeartbeatWorkflowPermitInput(BaseModel):
    """Input for refreshing a workflow permit TTL."""

    org_id: str
    """Organization ID (as string for serialization)."""
    workflow_id: str
    """Workflow execution ID."""


class GetTierLimitsInput(BaseModel):
    """Input for getting tier limits."""

    org_id: str
    """Organization ID (as string for serialization)."""


class AcquireActionPermitInput(BaseModel):
    """Input for acquiring an action execution permit."""

    org_id: str
    """Organization ID (as string for serialization)."""
    action_id: str
    """Action execution ID."""
    limit: int
    """Maximum concurrent action executions allowed."""


class ReleaseActionPermitInput(BaseModel):
    """Input for releasing an action execution permit."""

    org_id: str
    """Organization ID (as string for serialization)."""
    action_id: str
    """Action execution ID."""


class HeartbeatActionPermitInput(BaseModel):
    """Input for refreshing an action execution permit TTL."""

    org_id: str
    """Organization ID (as string for serialization)."""
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
    redis_client = await get_redis_client()
    # Get the underlying redis.Redis client for the semaphore
    client = await redis_client._get_client()
    semaphore = RedisSemaphore(client)

    result = await semaphore.acquire_workflow(
        org_id=input.org_id,  # type: ignore[arg-type]
        workflow_id=input.workflow_id,
        limit=input.limit,
    )

    logger.info(
        "Workflow permit acquire attempt",
        org_id=input.org_id,
        workflow_id=input.workflow_id,
        limit=input.limit,
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
        org_id=input.org_id,  # type: ignore[arg-type]
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
    redis_client = await get_redis_client()
    client = await redis_client._get_client()
    semaphore = RedisSemaphore(client)

    return await semaphore.heartbeat_workflow(
        org_id=input.org_id,  # type: ignore[arg-type]
        workflow_id=input.workflow_id,
    )


@activity.defn
async def acquire_action_permit_activity(
    input: AcquireActionPermitInput,
) -> AcquireResult:
    """Try to acquire an action execution permit."""
    redis_client = await get_redis_client()
    client = await redis_client._get_client()
    semaphore = RedisSemaphore(client)

    result = await semaphore.acquire_action(
        org_id=input.org_id,  # type: ignore[arg-type]
        action_id=input.action_id,
        limit=input.limit,
    )

    logger.info(
        "Action permit acquire attempt",
        org_id=input.org_id,
        action_id=input.action_id,
        limit=input.limit,
        acquired=result.acquired,
        current_count=result.current_count,
    )
    return result


@activity.defn
async def release_action_permit_activity(
    input: ReleaseActionPermitInput,
) -> None:
    """Release an action execution permit."""
    redis_client = await get_redis_client()
    client = await redis_client._get_client()
    semaphore = RedisSemaphore(client)

    await semaphore.release_action(
        org_id=input.org_id,  # type: ignore[arg-type]
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
    redis_client = await get_redis_client()
    client = await redis_client._get_client()
    semaphore = RedisSemaphore(client)

    return await semaphore.heartbeat_action(
        org_id=input.org_id,  # type: ignore[arg-type]
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
    async with get_async_session_context_manager() as session:
        service = TierService(session)
        limits = await service.get_effective_limits(input.org_id)  # type: ignore[arg-type]

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
