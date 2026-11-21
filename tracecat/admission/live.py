"""Admission for work that waits in-process instead of in workflow code.

A live agent tool call can't wait durably: the model is waiting on it. It waits
a few seconds here, then gives up with ``CapacityBusyError``. Polling the lease
count first means a full organization costs Redis reads, not Temporal starts;
the executor's gate stays the authority on admission.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Final

from temporalio import activity

from tracecat.admission.gate import ATTEMPT_TIMEOUT_SECONDS, Lease
from tracecat.admission.leases import LeaseStore
from tracecat.admission.limits import resolve_limit
from tracecat.admission.types import Busy, ConcurrencyResource, NotAdmitted
from tracecat.feature_flags import FeatureFlag, is_feature_enabled
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger

POLL_INTERVAL_SECONDS: Final = 0.5
"""Pause between capacity checks while waiting."""

RENEW_INTERVAL_SECONDS: Final = 30.0
"""Longest gap between lease renewals; well under the 90 s lease expiry."""


class CapacityBusyError(Exception):
    """The organization stayed at its limit for the whole wait; nothing ran."""

    def __init__(
        self,
        *,
        resource: ConcurrencyResource,
        in_use: int,
        limit: int,
        waited_seconds: float,
    ) -> None:
        super().__init__(
            f"Organization is at its {resource.value} concurrency limit "
            f"({in_use}/{limit} in use)"
        )
        self.resource = resource
        self.in_use = in_use
        self.limit = limit
        self.waited_seconds = waited_seconds


async def _at_limit(
    org_id: OrganizationID, resource: ConcurrencyResource
) -> Busy | None:
    """Return ``Busy`` when the organization is at its limit.

    ``None`` means there is room or the answer is unknown (no limit, flag off,
    lookup or Redis error); the caller then lets the gate decide.
    """
    if not is_feature_enabled(FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS):
        return None
    try:
        async with asyncio.timeout(ATTEMPT_TIMEOUT_SECONDS):
            limit = await resolve_limit(org_id, resource)
            if limit is None or limit <= 0:
                return None
            store = await LeaseStore.create()
            in_use = await store.in_use(org_id, resource)
    except Exception:
        return None
    return Busy(in_use=in_use, limit=limit) if in_use >= limit else None


async def run_with_capacity_wait[T](
    run: Callable[[], Awaitable[T | NotAdmitted]],
    *,
    org_id: OrganizationID,
    resource: ConcurrencyResource,
    wait_seconds: float,
) -> T:
    """Run ``run`` once there is capacity, waiting at most ``wait_seconds``.

    ``run`` is called again if its attempt was not admitted (another call took
    the last unit first) and time remains.

    Raises:
        CapacityBusyError: The organization stayed at its limit for the wait.
    """
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    give_up_at = started_at + wait_seconds
    waited = False
    while True:
        busy = await _at_limit(org_id, resource)
        if busy is None:
            # Measured before the call: the wait, not the work.
            waited_seconds = loop.time() - started_at
            result = await run()
            if not isinstance(result, NotAdmitted):
                if waited:
                    logger.info(
                        "concurrency.admitted_after_wait",
                        org_id=str(org_id),
                        resource=resource.value,
                        wait_seconds=waited_seconds,
                    )
                return result
            busy = Busy(in_use=result.in_use, limit=result.limit)
        # ``not > 0`` also gives up on a NaN wait setting instead of spinning.
        if not (remaining := give_up_at - loop.time()) > 0:
            waited_seconds = loop.time() - started_at
            logger.warning(
                "concurrency.overload",
                org_id=str(org_id),
                resource=resource.value,
                in_use=busy.in_use,
                limit=busy.limit,
                wait_seconds=waited_seconds,
            )
            raise CapacityBusyError(
                resource=resource,
                in_use=busy.in_use,
                limit=busy.limit,
                waited_seconds=waited_seconds,
            )
        waited = True
        # The last check lands on the deadline, so the whole wait is used.
        await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))


def activity_lease_id(info: activity.Info) -> str:
    """Attempt-specific lease ID for an activity: a retry never reuses a lease."""
    run_id = info.workflow_run_id or info.workflow_id or "no-workflow"
    return f"{run_id}:{info.activity_id}:{info.attempt}"


def activity_lease_deadline(info: activity.Info, *, fallback: timedelta) -> datetime:
    """When an activity attempt must end; a lease never outlives it."""
    if info.start_to_close_timeout is not None:
        return info.started_time + info.start_to_close_timeout
    if info.schedule_to_close_timeout is not None:
        return info.scheduled_time + info.schedule_to_close_timeout
    return datetime.now(UTC) + fallback


@asynccontextmanager
async def holding(lease: Lease | None) -> AsyncGenerator[None]:
    """Renew ``lease`` every 30 s while the body runs, then release it.

    ``None`` (running uncounted) does nothing.
    """
    if lease is None:
        yield
        return

    async def renew_forever() -> None:
        while True:
            await asyncio.sleep(RENEW_INTERVAL_SECONDS)
            await lease.renew()

    renewal = asyncio.create_task(renew_forever())
    try:
        yield
    finally:
        renewal.cancel()
        with suppress(asyncio.CancelledError):
            await renewal
        await lease.release()
