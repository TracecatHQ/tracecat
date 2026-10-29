"""Managed variables and secrets: the package owns the shape, the customer the values.

Git carries only a variable's key names and a secret's metadata, never values.
So on a managed variable or secret the customer may set and rotate values for
the existing keys, and point an AWS-backed secret at their own ARN or secret
name. Everything else (name, environment, the set of keys, type, store and key
mapping, description, tags, deletion) is locked.

Example: a managed secret ``threatintel`` has key ``API_KEY``. Updating it with
``keys=[{"key": "API_KEY", "value": "..."}]`` is allowed. Adding a second key,
renaming it, or deleting it raises :class:`ManagedResourceLockedError`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.managed_resources.guard import (
    ManagedResourceLockedError,
    bypass_active,
    is_managed,
)
from tracecat.workspace_sync.enums import SyncResourceType


async def ensure_values_only_update(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
    resource_type: SyncResourceType,
    local_id: uuid.UUID,
    fields_set: Collection[str],
    allowed_fields: Collection[str],
    current_keys: Callable[[], Iterable[str]] | None = None,
    requested_keys: Iterable[str] | None = None,
) -> None:
    """Refuse an update that changes more than values on a managed resource.

    Args:
        fields_set: The update fields the caller sent.
        allowed_fields: Fields a customer may change, e.g. ``{"values"}``.
        current_keys: Returns the key names the resource has now. Called only
            for managed resources, since reading them may decrypt values.
        requested_keys: Key names the update would leave, when it sends keys.

    Raises:
        ManagedResourceLockedError: The resource is managed and the update
            changes a locked field or the set of keys.
    """
    if workspace_id is None or bypass_active():
        return
    if not await is_managed(
        session,
        workspace_id=workspace_id,
        resource_type=resource_type,
        local_id=local_id,
    ):
        return
    if set(fields_set) - set(allowed_fields):
        raise ManagedResourceLockedError(resource_type, local_id)
    if requested_keys is None:
        return
    if set(requested_keys) != set(current_keys() if current_keys else ()):
        raise ManagedResourceLockedError(resource_type, local_id)
