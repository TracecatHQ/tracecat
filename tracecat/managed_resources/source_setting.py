"""The stored managed resources source setting.

Kept apart from :mod:`tracecat.managed_resources.source` so workspace sync can
ask "is this workspace the package source?" without importing the sync
service back.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import PlatformSetting, Workspace
from tracecat.exceptions import TracecatValidationError
from tracecat.workspace_sync.enums import VcsProvider

MANAGED_RESOURCES_SOURCE_KEY = "managed_resources_source"
"""Platform setting key holding a :class:`ManagedSourceSetting` JSON object."""


class ManagedSourceSetting(BaseModel):
    """The stored source: the workspace and the Git binding pinned with it.

    Pinning the binding means someone with ``workspace:update`` in the source
    organization cannot repoint the managed repository without a platform
    admin; any change to the live binding makes the source invalid instead.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: uuid.UUID
    git_provider: VcsProvider
    git_repo_url: str = Field(min_length=1)


async def managed_source_workspace_id(session: AsyncSession) -> uuid.UUID | None:
    """Return the configured source workspace id, or ``None``.

    A malformed stored value counts as unset here. Callers that must report
    it use :func:`tracecat.managed_resources.source.get_managed_source`.
    """
    raw = await session.scalar(
        select(PlatformSetting.value).where(
            PlatformSetting.key == MANAGED_RESOURCES_SOURCE_KEY
        )
    )
    if raw is None:
        return None
    try:
        return ManagedSourceSetting.model_validate_json(raw).workspace_id
    except PydanticValidationError:
        return None


async def is_managed_source(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Return whether ``workspace_id`` is the managed resources source.

    Raises:
        TracecatValidationError: It is the source, but its live Git binding no
            longer matches the one pinned with it. Fails closed, so neither
            package nor ordinary sync runs against an unapproved repository.
    """
    raw = await session.scalar(
        select(PlatformSetting.value).where(
            PlatformSetting.key == MANAGED_RESOURCES_SOURCE_KEY
        )
    )
    if raw is None:
        return False
    try:
        pinned = ManagedSourceSetting.model_validate_json(raw)
    except PydanticValidationError:
        return False
    if pinned.workspace_id != workspace_id:
        return False
    settings = await session.scalar(
        select(Workspace.settings).where(Workspace.id == workspace_id)
    )
    try:
        live = _LiveBinding.model_validate(settings or {})
    except PydanticValidationError:
        live = _LiveBinding()
    if (live.git_provider, live.git_repo_url) != (
        pinned.git_provider,
        pinned.git_repo_url,
    ):
        raise TracecatValidationError(
            "This workspace's Git binding changed after it was set as the managed "
            "resources source. A platform admin must set the source again."
        )
    return True


class _LiveBinding(BaseModel):
    """The Git sync fields of workspace settings; other settings are ignored."""

    model_config = ConfigDict(extra="ignore")

    git_provider: VcsProvider | None = None
    git_repo_url: str | None = None
