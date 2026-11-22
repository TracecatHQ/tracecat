"""Admission gate: take a concurrency lease before any effect.

The gate never waits for capacity. It admits or raises ``NotAdmittedError``.
When it gets no answer it tells two cases apart:

- **Redis outage**: Redis returns an error, or times out while this process's
  event loop is responsive. The gate retries for about 2 s, then lets the work
  run uncounted (fail open), logs it, and skips Redis in this process for a
  while (the breaker).
- **Local overload**: Redis times out while the event loop is lagging, or the
  Redis connection pool is exhausted. Redis is likely fine; this process is
  slow. The gate releases any lease the attempt may hold and raises a deferred
  ``NotAdmittedError`` at once. The breaker is left alone.

A failed limit lookup never fails open: the gate serves a recently cached
limit or defers.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from tracecat import config
from tracecat.admission.leases import MAX_LEASE_TTL_SECONDS, LeaseStore
from tracecat.admission.limits import LimitUnavailableError, resolve_limit
from tracecat.admission.types import (
    UNKNOWN_COUNT,
    Admitted,
    Busy,
    ConcurrencyResource,
    Deferred,
    NotAdmittedReason,
    Unenforced,
)
from tracecat.feature_flags import FeatureFlag, is_feature_enabled
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.observability.loop_lag import sampler as loop_lag

FAIL_OPEN_AFTER_SECONDS: Final = 2.0
"""How long the gate retries an unavailable store before it fails open."""

ATTEMPT_TIMEOUT_SECONDS: Final = 1.0
"""Ceiling for one Redis acquire attempt; the limit lookup has its own."""

RETRY_BACKOFF_SECONDS: Final = 0.25
"""First pause between attempts; doubles each attempt."""

BREAKER_SECONDS: Final = 30.0
"""After a fail-open, skip the store for this long in this process."""

RENEW_TIMEOUT_SECONDS: Final = 2.0
RELEASE_TIMEOUT_SECONDS: Final = 2.0

_breaker_open_until: float = 0.0


class NotAdmittedError(Exception):
    """The attempt executed nothing and should be tried again later.

    ``reason`` is ``AT_LIMIT`` when the organization is at its limit. For a
    deferral without an answer from Redis, ``in_use`` and ``limit`` are
    ``UNKNOWN_COUNT``.
    """

    def __init__(
        self,
        *,
        resource: ConcurrencyResource,
        in_use: int,
        limit: int,
        reason: NotAdmittedReason = NotAdmittedReason.AT_LIMIT,
    ) -> None:
        if reason is NotAdmittedReason.AT_LIMIT:
            message = (
                f"Organization is at its {resource.value} concurrency limit "
                f"({in_use}/{limit} in use)"
            )
        else:
            message = f"Admission for {resource.value} deferred: {reason.value}"
        super().__init__(message)
        self.resource = resource
        self.in_use = in_use
        self.limit = limit
        self.reason = reason


def _ttl_seconds(deadline: datetime) -> float:
    remaining = (deadline - datetime.now(UTC)).total_seconds()
    return min(max(remaining, 1.0), MAX_LEASE_TTL_SECONDS)


@dataclass(slots=True)
class Lease:
    """A held lease. Renew it while running; the gate releases it."""

    store: LeaseStore
    org_id: OrganizationID
    resource: ConcurrencyResource
    lease_id: str
    units: int
    deadline: datetime

    async def renew(self) -> None:
        """Extend the lease to ``min(deadline, now + 90 s)``. Never raises."""
        try:
            async with asyncio.timeout(RENEW_TIMEOUT_SECONDS):
                held = await self.store.renew(
                    self.org_id,
                    self.resource,
                    self.lease_id,
                    units=self.units,
                    ttl_seconds=_ttl_seconds(self.deadline),
                )
        except Exception as e:
            logger.warning(
                "concurrency.renew_failed",
                org_id=str(self.org_id),
                resource=self.resource.value,
                lease_id=self.lease_id,
                error=str(e),
            )
            return
        if not held:
            logger.warning(
                "concurrency.lease_lost",
                org_id=str(self.org_id),
                resource=self.resource.value,
                lease_id=self.lease_id,
            )

    async def release(self) -> None:
        """Release the lease. Never raises; expiry covers a failed release."""
        try:
            async with asyncio.timeout(RELEASE_TIMEOUT_SECONDS):
                await self.store.release(
                    self.org_id, self.resource, self.lease_id, units=self.units
                )
        except Exception as e:
            logger.warning(
                "concurrency.release_failed",
                org_id=str(self.org_id),
                resource=self.resource.value,
                lease_id=self.lease_id,
                units=self.units,
                error=str(e),
            )


@dataclass(frozen=True, slots=True)
class _Unlimited:
    """No limit applies; run uncounted without touching the store."""


def _is_local_failure(error: Exception, store: LeaseStore | None) -> bool:
    """Whether a failed Redis step points at this process, not at Redis.

    A timeout while the event loop lags, or an exhausted connection pool, means
    the reply was late or never sent because this process is busy. Without a
    lag reading (no sampler on this loop) a timeout counts as an outage.
    """
    if isinstance(error, RedisConnectionError) and store is not None:
        return store.pool_exhausted()
    if isinstance(error, TimeoutError | RedisTimeoutError):
        lag = loop_lag.max_lag_seconds()
        return (
            lag is not None
            and lag >= config.TRACECAT__ADMISSION_OVERLOAD_LOOP_LAG_SECONDS
        )
    return False


async def _try_admit(
    *,
    org_id: OrganizationID,
    resource: ConcurrencyResource,
    lease_id: str,
    units: int,
    deadline: datetime,
) -> tuple[Admitted | Busy | Unenforced | Deferred | _Unlimited, Lease | None]:
    try:
        limit = await resolve_limit(org_id, resource)
    except LimitUnavailableError as e:
        return Deferred(reason=NotAdmittedReason.LIMIT_UNAVAILABLE, error=e), None
    if limit is None:
        return _Unlimited(), None
    if limit <= 0:
        # Tier APIs don't validate this yet. Never wait forever on a bad value.
        logger.error(
            "concurrency.invalid_limit",
            org_id=str(org_id),
            resource=resource.value,
            limit=limit,
        )
        return _Unlimited(), None
    units = min(units, limit)
    store: LeaseStore | None = None
    try:
        async with asyncio.timeout(ATTEMPT_TIMEOUT_SECONDS):
            store = await LeaseStore.create()
            admission = await store.try_acquire(
                org_id,
                resource,
                lease_id,
                units=units,
                limit=limit,
                ttl_seconds=_ttl_seconds(deadline),
            )
    except Exception as e:
        if _is_local_failure(e, store):
            return Deferred(reason=NotAdmittedReason.EXECUTOR_OVERLOADED, error=e), None
        return Unenforced(error=e), None
    if isinstance(admission, Busy):
        return admission, None
    return admission, Lease(
        store=store,
        org_id=org_id,
        resource=resource,
        lease_id=lease_id,
        units=units,
        deadline=deadline,
    )


async def _release_quietly(
    org_id: OrganizationID, resource: ConcurrencyResource, lease_id: str, units: int
) -> None:
    """Best-effort release of a lease whose acquire reply may have been lost."""
    try:
        async with asyncio.timeout(RELEASE_TIMEOUT_SECONDS):
            store = await LeaseStore.create()
            await store.release(org_id, resource, lease_id, units=max(1, units))
    except Exception:
        # Expiry (at most 90 s) covers it.
        pass


def _fail_open(
    *,
    org_id: OrganizationID,
    resource: ConcurrencyResource,
    reason: str,
    error: BaseException | None = None,
) -> None:
    logger.warning(
        "concurrency.fail_open",
        org_id=str(org_id),
        resource=resource.value,
        reason=reason,
        error=str(error) if error is not None else None,
    )


async def acquire(
    *,
    org_id: OrganizationID,
    resource: ConcurrencyResource,
    lease_id: str,
    units: int,
    deadline: datetime,
) -> Lease | None:
    """Take a lease, or return ``None`` when the work should run uncounted.

    Raises:
        NotAdmittedError: The organization is at its limit, or the attempt was
            deferred (executor overloaded or limit unavailable).
    """
    global _breaker_open_until

    if not is_feature_enabled(FeatureFlag.WORKFLOW_CONCURRENCY_LIMITS):
        return None
    if time.monotonic() < _breaker_open_until:
        _fail_open(org_id=org_id, resource=resource, reason="breaker_open")
        return None

    give_up_at = time.monotonic() + FAIL_OPEN_AFTER_SECONDS
    backoff = RETRY_BACKOFF_SECONDS
    while True:
        admission, lease = await _try_admit(
            org_id=org_id,
            resource=resource,
            lease_id=lease_id,
            units=max(1, units),
            deadline=deadline,
        )
        match admission:
            case _Unlimited():
                return None
            case Admitted(in_use=in_use, limit=limit):
                logger.debug(
                    "concurrency.admitted",
                    org_id=str(org_id),
                    resource=resource.value,
                    lease_id=lease_id,
                    in_use=in_use,
                    limit=limit,
                )
                return lease
            case Busy(in_use=in_use, limit=limit):
                logger.info(
                    "concurrency.busy",
                    org_id=str(org_id),
                    resource=resource.value,
                    lease_id=lease_id,
                    in_use=in_use,
                    limit=limit,
                )
                raise NotAdmittedError(resource=resource, in_use=in_use, limit=limit)
            case Deferred(reason=reason, error=error):
                if reason is NotAdmittedReason.EXECUTOR_OVERLOADED:
                    # The acquire may have run with its reply lost.
                    await _release_quietly(org_id, resource, lease_id, units)
                logger.warning(
                    "concurrency.deferred",
                    org_id=str(org_id),
                    resource=resource.value,
                    lease_id=lease_id,
                    reason=reason.value,
                    loop_lag_seconds=loop_lag.max_lag_seconds(),
                    error=repr(error),
                )
                raise NotAdmittedError(
                    resource=resource,
                    in_use=UNKNOWN_COUNT,
                    limit=UNKNOWN_COUNT,
                    reason=reason,
                )
            case Unenforced(error=error):
                if time.monotonic() + backoff >= give_up_at:
                    # Redis is shared by every org: skip it for a while.
                    _breaker_open_until = time.monotonic() + BREAKER_SECONDS
                    # The acquire may have run with its reply lost.
                    await _release_quietly(org_id, resource, lease_id, units)
                    _fail_open(
                        org_id=org_id,
                        resource=resource,
                        reason="store_unavailable",
                        error=error,
                    )
                    return None
                await asyncio.sleep(backoff)
                backoff *= 2


@asynccontextmanager
async def admit(
    *,
    org_id: OrganizationID,
    resource: ConcurrencyResource,
    lease_id: str,
    units: int,
    deadline: datetime,
) -> AsyncGenerator[Lease | None]:
    """Hold a lease for the body; yields ``None`` when running uncounted.

    Raises:
        NotAdmittedError: The organization is at its limit. Raised before the
            body runs.
    """
    lease = await acquire(
        org_id=org_id,
        resource=resource,
        lease_id=lease_id,
        units=units,
        deadline=deadline,
    )
    try:
        yield lease
    finally:
        if lease is not None:
            await lease.release()


def reset_breaker() -> None:
    """Close the fail-open breaker (tests)."""
    global _breaker_open_until
    _breaker_open_until = 0.0
