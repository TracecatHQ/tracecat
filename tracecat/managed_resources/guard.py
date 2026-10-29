"""The managed write guard: refuse changes to resources a rollout owns.

A managed resource is locked for every role in the target organization,
admins included. Each resource service calls :func:`ensure_unmanaged` before
it updates or deletes, so the API, the SDK and internal callers all hit the
same check. The rollout engine and detach are the only writers allowed
through, and they say so explicitly with :func:`managed_write_bypass`, never
with a role flag.

Example: an org owner PATCHes a managed workflow's title. The service calls
``ensure_unmanaged(session, workspace_id, SyncResourceType.WORKFLOW, id)``,
which finds a ``managed_resource`` row and raises
:class:`ManagedResourceLockedError`; the API answers 403 with
"Managed by Tracecat. Detach to edit."
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import ManagedResource
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.workspace_sync.enums import SyncResourceType

LOCKED_MESSAGE = "Managed by Tracecat. Detach to edit."

_bypass: ContextVar[bool] = ContextVar("managed_write_bypass", default=False)


class ManagedResourceLockedError(TracecatAuthorizationError):
    """A caller tried to change a managed resource."""

    def __init__(self, resource_type: SyncResourceType, local_id: uuid.UUID) -> None:
        super().__init__(
            LOCKED_MESSAGE,
            detail={"resource_type": resource_type.value, "local_id": str(local_id)},
        )
        self.resource_type = resource_type
        self.local_id = local_id


@contextmanager
def managed_write_bypass() -> Iterator[None]:
    """Let the rollout engine or detach write managed resources.

    Only those two internal paths may enter this context. It is a context
    variable, so it covers exactly the code awaited inside the block.
    """
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)


def bypass_active() -> bool:
    """Return whether a managed-write bypass covers the current code."""
    return _bypass.get()


async def is_managed(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    resource_type: SyncResourceType,
    local_id: uuid.UUID,
) -> bool:
    """Return whether one resource is managed, in one indexed query."""
    return bool(
        await session.scalar(
            select(
                exists().where(
                    ManagedResource.workspace_id == workspace_id,
                    ManagedResource.resource_type == resource_type.value,
                    ManagedResource.local_id == local_id,
                )
            )
        )
    )


async def managed_local_ids(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    resource_type: SyncResourceType,
    local_ids: Collection[uuid.UUID] | None = None,
) -> set[uuid.UUID]:
    """Return which of ``local_ids`` are managed, or every managed id if ``None``."""
    stmt = select(ManagedResource.local_id).where(
        ManagedResource.workspace_id == workspace_id,
        ManagedResource.resource_type == resource_type.value,
    )
    if local_ids is not None:
        if not local_ids:
            return set()
        stmt = stmt.where(ManagedResource.local_id.in_(local_ids))
    return set(await session.scalars(stmt))


async def ensure_unmanaged(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
    resource_type: SyncResourceType,
    local_id: uuid.UUID,
) -> None:
    """Refuse a change to a managed resource unless a bypass is active.

    Raises:
        ManagedResourceLockedError: The resource is managed.
    """
    if workspace_id is None or bypass_active():
        return
    if await is_managed(
        session,
        workspace_id=workspace_id,
        resource_type=resource_type,
        local_id=local_id,
    ):
        raise ManagedResourceLockedError(resource_type, local_id)


async def ensure_none_managed(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
    resource_type: SyncResourceType,
    local_ids: Collection[uuid.UUID],
) -> None:
    """Refuse a batch change if any resource in it is managed.

    Raises:
        ManagedResourceLockedError: For the first managed resource found.
    """
    if workspace_id is None or bypass_active() or not local_ids:
        return
    managed = await managed_local_ids(
        session,
        workspace_id=workspace_id,
        resource_type=resource_type,
        local_ids=local_ids,
    )
    if managed:
        raise ManagedResourceLockedError(resource_type, min(managed))
