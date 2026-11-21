"""Observed usage of per-organization concurrency limits."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from tracecat.admission.leases import LeaseStore
from tracecat.admission.types import ConcurrencyResource
from tracecat.feature_flags import FeatureFlag, is_feature_enabled
from tracecat.logger import logger
from tracecat.tiers.enums import ConcurrencyLimitName
from tracecat.tiers.types import ConcurrencyUsage

if TYPE_CHECKING:
    from tracecat.identifiers import OrganizationID
    from tracecat.tiers.schemas import EffectiveLimits

USAGE_READ_TIMEOUT_SECONDS: Final = 2.0


def _leased_limits(
    limits: EffectiveLimits,
) -> dict[ConcurrencyLimitName, ConcurrencyResource]:
    """Limits counted by leases, for the ones that are set.

    An unlimited resource takes no leases, so its usage is unknown, not zero.
    """
    leased = {
        ConcurrencyLimitName.MAX_CONCURRENT_TOOL_CALLS: (
            ConcurrencyResource.TOOL_CALLS,
            limits.max_concurrent_tool_calls,
        ),
        ConcurrencyLimitName.MAX_CONCURRENT_AGENT_TURNS: (
            ConcurrencyResource.AGENT_TURNS,
            limits.max_concurrent_agent_turns,
        ),
    }
    return {
        name: resource
        for name, (resource, limit) in leased.items()
        if limit is not None and limit > 0
    }


async def read_concurrency_usage(
    org_id: OrganizationID,
    limits: EffectiveLimits,
) -> dict[ConcurrencyLimitName, ConcurrencyUsage]:
    """Read the observed usage of an organization's concurrency limits.

    Every usage read goes through this function, so callers never touch the
    lease store or the run ledger directly. A limit missing from the result has
    unknown usage, which callers must report as unknown, never as zero.

    Agent tool calls and agent turns are read from the lease store when their
    limit is set and enforcement is on; other limits aren't recorded yet.

    Args:
        org_id: The organization to read usage for.
        limits: The organization's effective limits.

    Returns:
        Usage keyed by limit, for the limits whose usage could be read.
    """
    if not is_feature_enabled(FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS):
        return {}
    if not (leased := _leased_limits(limits)):
        return {}
    try:
        async with asyncio.timeout(USAGE_READ_TIMEOUT_SECONDS):
            store = await LeaseStore.create()
            counts = await asyncio.gather(
                *(store.in_use(org_id, resource) for resource in leased.values())
            )
    except Exception as e:
        logger.warning(
            "concurrency.usage_unavailable",
            org_id=str(org_id),
            error=str(e),
        )
        return {}
    observed_at = datetime.now(UTC)
    return {
        name: ConcurrencyUsage(in_use=count, observed_at=observed_at)
        for name, count in zip(leased, counts, strict=True)
    }
