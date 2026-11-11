"""Managed agent presets: the one thing customers may change is added skills.

A managed preset is locked like every managed resource, with one exception:
the customer may attach their own skills to it and detach them again. The
package's own bindings are recorded in ``managed_preset_skill_binding``;
every other binding on a managed preset belongs to the customer.

Example: the package binds skills A and B, and the customer adds C. A
rollout of a tag that binds A and D leaves A, D and C. The customer can
never remove A.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.preset.types import SkillBindingSpec
from tracecat.db.models import ManagedPresetSkillBinding
from tracecat.managed_resources.guard import (
    ManagedResourceLockedError,
    bypass_active,
    is_managed,
)
from tracecat.workspace_sync.enums import SyncResourceType

_customer_bindings: ContextVar[
    Mapping[uuid.UUID, Sequence[SkillBindingSpec]] | None
] = ContextVar("managed_customer_skill_bindings", default=None)


async def managed_skill_ids(
    session: AsyncSession, *, workspace_id: uuid.UUID, preset_id: uuid.UUID
) -> set[uuid.UUID]:
    """Return the skills the package binds to a managed preset."""
    return set(
        await session.scalars(
            select(ManagedPresetSkillBinding.skill_id).where(
                ManagedPresetSkillBinding.workspace_id == workspace_id,
                ManagedPresetSkillBinding.preset_id == preset_id,
            )
        )
    )


async def ensure_preset_update_allowed(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None,
    preset_id: uuid.UUID,
    fields_set: set[str],
    requested_skill_ids: Sequence[uuid.UUID] | None,
) -> None:
    """Refuse a preset update that changes more than the customer's skills.

    Args:
        fields_set: The update fields the caller sent.
        requested_skill_ids: The full new skill list, when ``skills`` is set.

    Raises:
        ManagedResourceLockedError: The preset is managed and the update
            touches another field or removes a package skill.
    """
    if workspace_id is None or bypass_active():
        return
    if not await is_managed(
        session,
        workspace_id=workspace_id,
        resource_type=SyncResourceType.AGENT_PRESET,
        local_id=preset_id,
    ):
        return
    if fields_set - {"skills"}:
        raise ManagedResourceLockedError(SyncResourceType.AGENT_PRESET, preset_id)
    if requested_skill_ids is None:
        return
    package_skills = await managed_skill_ids(
        session, workspace_id=workspace_id, preset_id=preset_id
    )
    if not package_skills <= set(requested_skill_ids):
        raise ManagedResourceLockedError(SyncResourceType.AGENT_PRESET, preset_id)


@contextmanager
def preserve_customer_skill_bindings(
    bindings: Mapping[uuid.UUID, Sequence[SkillBindingSpec]],
) -> Iterator[None]:
    """Keep customer skill bindings while a rollout reconciles presets.

    ``bindings`` maps a preset id to the customer's bindings on it. Inside
    the block, :func:`with_customer_skill_bindings` adds them to whatever
    bindings the package declares.
    """
    token = _customer_bindings.set(bindings)
    try:
        yield
    finally:
        _customer_bindings.reset(token)


def with_customer_skill_bindings(
    preset_id: uuid.UUID, package_bindings: Sequence[SkillBindingSpec]
) -> list[SkillBindingSpec]:
    """Return the package bindings plus any preserved customer bindings.

    A skill the package now binds itself keeps the package's binding; the
    customer's duplicate is dropped.
    """
    merged = list(package_bindings)
    package_skills = {binding.skill_id for binding in package_bindings}
    merged.extend(
        binding
        for binding in (_customer_bindings.get() or {}).get(preset_id, ())
        if binding.skill_id not in package_skills
    )
    return merged
