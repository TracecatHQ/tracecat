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

import hashlib
import uuid
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import ManagedResource
from tracecat.db.tenant_rls import RLS_BYPASS_ON, RLS_BYPASS_VAR
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.workspace_sync.enums import SyncResourceType

LOCKED_MESSAGE = "Managed by Tracecat. Detach to edit."

_bypass: ContextVar[bool] = ContextVar("managed_write_bypass", default=False)


class ManagedResourceLockedError(TracecatAuthorizationError):
    """A caller tried to change a managed resource."""

    def __init__(
        self, resource_type: SyncResourceType | str, local_id: uuid.UUID
    ) -> None:
        resource_type_value = (
            resource_type.value
            if isinstance(resource_type, SyncResourceType)
            else resource_type
        )
        super().__init__(
            LOCKED_MESSAGE,
            detail={"resource_type": resource_type_value, "local_id": str(local_id)},
        )
        self.resource_type = resource_type_value
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


def ownership_lock_key(workspace_id: uuid.UUID) -> int:
    """Advisory lock key that serializes ownership changes in one workspace."""
    digest = hashlib.sha256(f"managed-resources:{workspace_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


async def hold_ownership_lock(
    session: AsyncSession, workspace_id: uuid.UUID, *, shared: bool
) -> None:
    """Hold the workspace's ownership lock until the transaction ends.

    Guard checks take it shared, so a check and the write after it finish
    before a rollout can record new ownership. A rollout takes it exclusive
    while it imports and records managed rows, so no customer write can pass
    the check and land after the rollout claims the resource.
    """
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await session.execute(
        text(f"SELECT {function}(:key)"), {"key": ownership_lock_key(workspace_id)}
    )


async def is_managed(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    resource_type: SyncResourceType,
    local_id: uuid.UUID,
) -> bool:
    """Return whether one resource is managed, in one indexed query.

    Takes the workspace's ownership lock shared first; see
    :func:`hold_ownership_lock`.
    """
    await hold_ownership_lock(session, workspace_id, shared=True)
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
    await hold_ownership_lock(session, workspace_id, shared=True)
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


REGISTRY_REPOSITORY_RESOURCE_TYPE = "registry_repository"
"""``managed_resource.resource_type`` of a registry repository a package installed."""


async def ensure_registry_repository_unmanaged(
    session: AsyncSession, repository_id: uuid.UUID
) -> None:
    """Refuse a change to a registry repository a managed package installed.

    Registry repositories belong to an organization, but managed rows are
    readable only inside one workspace. So this one existence check runs in a
    savepoint with the RLS bypass on, and the savepoint is rolled back:
    Postgres undoes ``set_config`` with the savepoint, so the bypass never
    outlives the check.

    Raises:
        ManagedResourceLockedError: A package installed the repository.
    """
    if bypass_active():
        return
    savepoint = await session.begin_nested()
    try:
        await session.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": RLS_BYPASS_VAR, "value": RLS_BYPASS_ON},
        )
        installed = await session.scalar(
            select(
                exists().where(
                    ManagedResource.resource_type == REGISTRY_REPOSITORY_RESOURCE_TYPE,
                    ManagedResource.local_id == repository_id,
                )
            )
        )
    finally:
        await savepoint.rollback()
    if installed:
        raise ManagedResourceLockedError(
            REGISTRY_REPOSITORY_RESOURCE_TYPE, repository_id
        )
