"""Per-organization cap on concurrent DB work in one process.

One org's burst (for example many actions waiting on one contended lock) must
not take every Postgres backend. Each org gets an ``asyncio.Semaphore`` per
process; an operation takes one unit before it takes a connection and waits,
holding nothing, when the org is at its cap. After a short wait it is refused
with ``TenantBusyError``, before it has had any effect.

In-process on purpose: a Redis semaphore on every checkout would add a network
hop and a new failure mode to the hottest path. The fleet-wide bound is the
cap times the replica count.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextvars import ContextVar
from dataclasses import dataclass

from tracecat import config
from tracecat.db.exceptions import TenantBusyError
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger

TENANT_BUSY_RETRY_AFTER_SECONDS = 1
"""``Retry-After`` sent with a tenant-busy refusal."""
TENANT_BUSY_WAIT_SECONDS = 5.0
"""How long an operation over the org's cap waits, holding nothing, before it
is refused. The SDK then retries the refusal until the action ends."""


@dataclass(frozen=True, slots=True)
class _HeldUnits:
    task: asyncio.Task[object] | None
    organization_ids: frozenset[OrganizationID]


_ctx_held_units: ContextVar[_HeldUnits | None] = ContextVar(
    "org_session_gate_held_units",
    default=None,
)
"""The orgs whose units the current task holds.

Spawned tasks inherit a copy of this value, so it also records the holder's
task: only that task treats the units as held. A child task takes its own.
"""


@dataclass(slots=True)
class _OrgEntry:
    semaphore: asyncio.Semaphore
    users: int = 0
    """Holders plus waiters; the entry is dropped when this reaches zero."""


class OrgSessionGate:
    """Caps concurrent operations per organization in this process.

    Re-entrant per task: nested ``hold`` calls for the same org in the same
    task take no second unit, so a task never waits on itself.
    """

    def __init__(self, *, cap: int, wait_seconds: float) -> None:
        if cap < 1:
            raise ValueError("The per-org DB session cap must be at least 1")
        self.cap = cap
        self.wait_seconds = wait_seconds
        # A plain dict with idle entries dropped: an LRU could evict a
        # semaphore that is still held.
        self._entries: dict[OrganizationID, _OrgEntry] = {}

    @contextlib.asynccontextmanager
    async def hold(self, organization_id: OrganizationID) -> AsyncGenerator[None]:
        """Hold one unit for the org for the duration of the block.

        Raises:
            TenantBusyError: The org stayed at its cap for ``wait_seconds``.
        """
        task = asyncio.current_task()
        held = _ctx_held_units.get()
        held_by_task = (
            held.organization_ids
            if held is not None and held.task is task
            else frozenset[OrganizationID]()
        )
        if organization_id in held_by_task:
            yield
            return

        entry = self._entries.get(organization_id)
        if entry is None:
            entry = _OrgEntry(semaphore=asyncio.Semaphore(self.cap))
            self._entries[organization_id] = entry
        entry.users += 1
        try:
            await self._acquire(organization_id, entry.semaphore)
            try:
                _ctx_held_units.set(_HeldUnits(task, held_by_task | {organization_id}))
                yield
            finally:
                # Restore by value, not by token: the exit may run in a copy
                # of the context, where a token reset raises.
                _ctx_held_units.set(held)
                entry.semaphore.release()
        finally:
            entry.users -= 1
            if entry.users == 0:
                self._entries.pop(organization_id, None)

    async def _acquire(
        self,
        organization_id: OrganizationID,
        semaphore: asyncio.Semaphore,
    ) -> None:
        try:
            async with asyncio.timeout(self.wait_seconds):
                await semaphore.acquire()
        except TimeoutError:
            logger.warning(
                "Organization at its DB session cap",
                event="db.org_gate.busy",
                organization_id=str(organization_id),
                cap=self.cap,
                wait_seconds=self.wait_seconds,
            )
            raise TenantBusyError(
                "Organization is at its database session cap",
                organization_id=organization_id,
                cap=self.cap,
                retry_after_seconds=TENANT_BUSY_RETRY_AFTER_SECONDS,
            ) from None


_gate: OrgSessionGate | None = None


def get_org_session_gate() -> OrgSessionGate | None:
    """The process's gate, or ``None`` when no cap is configured."""
    global _gate
    if (cap := config.TRACECAT__DB_ORG_SESSION_CAP) is None:
        return None
    if _gate is None:
        _gate = OrgSessionGate(
            cap=cap,
            wait_seconds=TENANT_BUSY_WAIT_SECONDS,
        )
    return _gate
