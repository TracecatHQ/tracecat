"""Reusable permit orchestration for tier-based concurrency controls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self

from tracecat import config
from tracecat.db.engine import get_async_session_context_manager
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.redis.client import get_redis_client
from tracecat.tiers.exceptions import InvalidOrganizationConcurrencyCapError
from tracecat.tiers.limits_cache import (
    get_effective_limits_cached,
    set_effective_limits_cached,
)
from tracecat.tiers.schemas import EffectiveLimits
from tracecat.tiers.semaphore import AcquireResult, RedisSemaphore
from tracecat.tiers.service import TierService

PermitScope = Literal["workflow", "action"]
_UNBOUNDED_CONCURRENCY_LIMIT = 2_147_483_647


@dataclass(frozen=True)
class PermitAcquireOutcome:
    """Metadata returned for permit acquisition attempts."""

    result: AcquireResult
    effective_limit: int
    cap_source: str


class TierPermitService:
    """Reusable service for tier permit acquire/release/heartbeat operations."""

    def __init__(self, semaphore: RedisSemaphore) -> None:
        self._semaphore = semaphore

    @classmethod
    async def create(cls) -> Self:
        """Create a service using the process Redis client and configured permit TTL."""
        redis_client = await get_redis_client()
        client = await redis_client._get_client()
        semaphore = RedisSemaphore(
            client, ttl_seconds=config.TRACECAT__PERMIT_TTL_SECONDS
        )
        return cls(semaphore)

    async def acquire_workflow_permit(
        self,
        *,
        org_id: OrganizationID,
        workflow_id: str,
    ) -> PermitAcquireOutcome:
        """Acquire or reject a workflow permit based on effective limits."""
        limits, cap_source = await self.get_effective_limits_for_org(org_id)
        effective_limit = self.normalize_concurrency_limit(
            limit=limits.max_concurrent_workflows,
            org_id=org_id,
            scope="workflow",
        )
        result = await self._semaphore.acquire_workflow(
            org_id=org_id,
            workflow_id=workflow_id,
            limit=effective_limit,
        )
        return PermitAcquireOutcome(
            result=result,
            effective_limit=effective_limit,
            cap_source=cap_source,
        )

    async def release_workflow_permit(
        self,
        *,
        org_id: OrganizationID,
        workflow_id: str,
    ) -> None:
        """Release a workflow permit."""
        await self._semaphore.release_workflow(
            org_id=org_id,
            workflow_id=workflow_id,
        )

    async def heartbeat_workflow_permit(
        self,
        *,
        org_id: OrganizationID,
        workflow_id: str,
    ) -> bool:
        """Heartbeat a workflow permit lease."""
        return await self._semaphore.heartbeat_workflow(
            org_id=org_id,
            workflow_id=workflow_id,
        )

    async def acquire_action_permit(
        self,
        *,
        org_id: OrganizationID,
        action_id: str,
    ) -> PermitAcquireOutcome:
        """Acquire or reject an action permit based on effective limits."""
        limits, cap_source = await self.get_effective_limits_for_org(org_id)
        effective_limit = self.normalize_concurrency_limit(
            limit=limits.max_concurrent_actions,
            org_id=org_id,
            scope="action",
        )
        result = await self._semaphore.acquire_action(
            org_id=org_id,
            action_id=action_id,
            limit=effective_limit,
        )
        return PermitAcquireOutcome(
            result=result,
            effective_limit=effective_limit,
            cap_source=cap_source,
        )

    async def release_action_permit(
        self,
        *,
        org_id: OrganizationID,
        action_id: str,
    ) -> None:
        """Release an action permit."""
        await self._semaphore.release_action(
            org_id=org_id,
            action_id=action_id,
        )

    async def heartbeat_action_permit(
        self,
        *,
        org_id: OrganizationID,
        action_id: str,
    ) -> bool:
        """Heartbeat an action permit lease."""
        return await self._semaphore.heartbeat_action(
            org_id=org_id,
            action_id=action_id,
        )

    async def get_effective_limits_for_org(
        self,
        org_id: OrganizationID,
    ) -> tuple[EffectiveLimits, str]:
        """Resolve effective limits via cache first, then DB fallback."""
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

    @staticmethod
    def normalize_concurrency_limit(
        *,
        limit: int | None,
        org_id: OrganizationID,
        scope: PermitScope,
    ) -> int:
        """Normalize nullable concurrency limits to usable semaphore limits."""
        if limit is None:
            return _UNBOUNDED_CONCURRENCY_LIMIT
        if limit <= 0:
            raise InvalidOrganizationConcurrencyCapError(
                scope=scope,
                org_id=org_id,
                limit=limit,
            )
        return limit
