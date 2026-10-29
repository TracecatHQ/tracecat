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

from tracecat.db.models import PlatformSetting
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
