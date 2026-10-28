"""The source workspace that managed resources are published from.

Tracecat engineers publish a curated package from exactly one source workspace
to that workspace's Git sync repository. Platform superusers later roll tags of
that repository out to workspaces in other organizations. This module names the
source workspace and reads its repository with the source organization's VCS
credentials on behalf of a platform superuser.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import PlatformRole, Role
from tracecat.db.models import PlatformSetting, Workspace
from tracecat.exceptions import (
    TracecatNotFoundError,
    TracecatSettingsError,
    TracecatValidationError,
)
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.logger import logger
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.service import WorkspaceSyncService

MANAGED_RESOURCES_SOURCE_KEY = "managed_resources_source"
"""Platform setting key holding a :class:`ManagedSourceSetting` JSON object."""

SOURCE_REPOSITORY_SCOPES: frozenset[str] = frozenset({"workspace_sync:sync"})
"""The only scope granted when reading the source repository.

It is the scope the org VCS credential getters and the workspace sync read
methods require, and it grants nothing else.
"""


class ManagedSourceNotConfiguredError(TracecatSettingsError):
    """No managed resources source workspace has been configured."""


class ManagedSourceInvalidError(TracecatSettingsError):
    """The configured source can no longer be used.

    The workspace was deleted, lost its Git binding, or had its binding changed
    after a platform admin pinned it, or the stored setting is malformed. A
    platform admin must set the source again.
    """


@dataclass(frozen=True, slots=True)
class ManagedSource:
    """A validated managed resources source workspace."""

    workspace_id: WorkspaceID
    organization_id: OrganizationID
    git_provider: VcsProvider
    git_repo_url: str


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

    @classmethod
    def pin(cls, source: ManagedSource) -> ManagedSourceSetting:
        """Snapshot a validated source for storage."""
        return cls(
            workspace_id=source.workspace_id,
            git_provider=source.git_provider,
            git_repo_url=source.git_repo_url,
        )


async def load_managed_source(
    session: AsyncSession, workspace_id: WorkspaceID
) -> ManagedSource:
    """Load a workspace and check that it can be the managed resources source.

    Args:
        session: A database session that can read any organization's workspace.
        workspace_id: The candidate source workspace.

    Raises:
        TracecatNotFoundError: The workspace does not exist.
        TracecatValidationError: The workspace has no Git sync binding.
    """
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    if workspace is None:
        raise TracecatNotFoundError("Workspace not found")
    settings = workspace.settings or {}
    raw_provider = settings.get("git_provider")
    repo_url = settings.get("git_repo_url")
    if not raw_provider or not repo_url:
        raise TracecatValidationError(
            "The source workspace must have both a Git provider and a Git "
            "repository URL configured"
        )
    try:
        provider = VcsProvider(raw_provider)
    except ValueError as e:
        raise TracecatValidationError(
            f"Unsupported Git provider configured for the source workspace: "
            f"{raw_provider}"
        ) from e
    return ManagedSource(
        workspace_id=workspace.id,
        organization_id=workspace.organization_id,
        git_provider=provider,
        git_repo_url=repo_url,
    )


async def get_managed_source(session: AsyncSession) -> ManagedSource | None:
    """Return the configured source workspace, or ``None`` if none is set.

    Raises:
        ManagedSourceInvalidError: The stored setting is malformed, or the
            workspace is gone, lost its Git binding, or no longer has the
            binding that was pinned when the source was set.
    """
    raw = await session.scalar(
        select(PlatformSetting.value).where(
            PlatformSetting.key == MANAGED_RESOURCES_SOURCE_KEY
        )
    )
    if raw is None:
        return None
    try:
        pinned = ManagedSourceSetting.model_validate_json(raw)
    except PydanticValidationError as e:
        raise ManagedSourceInvalidError(
            "The stored managed resources source setting is malformed"
        ) from e
    try:
        source = await load_managed_source(session, pinned.workspace_id)
    except (TracecatNotFoundError, TracecatValidationError) as e:
        raise ManagedSourceInvalidError(
            f"The managed resources source workspace is no longer valid: {e}"
        ) from e
    if ManagedSourceSetting.pin(source) != pinned:
        raise ManagedSourceInvalidError(
            "The managed resources source workspace's Git binding changed after "
            "it was set. A platform admin must set the source again."
        )
    return source


def source_repository_role(source: ManagedSource) -> Role:
    """Build the role used to read the source repository.

    The role is bound to the one configured source workspace and its owning
    organization, both read from the database rather than the request. It
    carries only :data:`SOURCE_REPOSITORY_SCOPES`, is not a platform superuser
    (so the ``*`` scope bypass never applies), and the source organization must
    still hold the Git sync entitlement.
    """
    return Role(
        type="service",
        service_id="tracecat-service",
        organization_id=source.organization_id,
        workspace_id=source.workspace_id,
        scopes=SOURCE_REPOSITORY_SCOPES,
    )


async def source_sync_service(
    session: AsyncSession, actor: PlatformRole
) -> WorkspaceSyncService:
    """Return a workspace sync service that reads the source repository.

    This is the only bridge from a platform superuser, who has no organization
    context, to the source organization's VCS credentials. It reuses
    :meth:`WorkspaceSyncService.for_workspace`, so provider selection, repository
    URL parsing and transport wiring match ordinary workspace sync.

    Args:
        session: A database session that can read across organizations.
        actor: The platform superuser making the request.

    Raises:
        ManagedSourceNotConfiguredError: No source workspace is configured.
        ManagedSourceInvalidError: The configured source can no longer be used.
    """
    source = await get_managed_source(session)
    if source is None:
        raise ManagedSourceNotConfiguredError(
            "Managed resources source workspace is not configured"
        )
    logger.info(
        "Using managed resources source repository",
        actor_id=actor.user_id,
        source_workspace_id=source.workspace_id,
        source_organization_id=source.organization_id,
    )
    return await WorkspaceSyncService.for_workspace(
        session, source_repository_role(source)
    )
