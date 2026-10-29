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
from tracecat.registry.repositories.schemas import GitTagInfo
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.service import (
    WorkspaceSyncService,
    parse_workspace_git_url,
)

MANAGED_RESOURCES_SOURCE_KEY = "managed_resources_source"
"""Platform setting key holding a :class:`ManagedSourceSetting` JSON object."""

SOURCE_REPOSITORY_SCOPES: frozenset[str] = frozenset({"workspace_sync:sync"})
"""The only scope granted when reading the source repository.

It is the scope the org VCS credential getters and the workspace sync read
methods require, and it grants nothing else.
"""


class ManagedSourceUnavailableError(TracecatSettingsError):
    """There is no usable managed resources source.

    Either none is configured, or the configured one can no longer be used:
    the workspace was deleted, lost its Git binding, or had its binding changed
    after a platform admin pinned it, or the stored setting is malformed. A
    platform admin must set the source (again).
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


class _GitBinding(BaseModel):
    """The Git sync fields of workspace settings; other settings are ignored."""

    model_config = ConfigDict(extra="ignore")

    git_provider: VcsProvider | None = None
    git_repo_url: str | None = None


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
    # Settings are JSONB, so a malformed row can hold anything. Validate only
    # the Git binding, so an unrelated bad setting does not block the source.
    try:
        settings = _GitBinding.model_validate(workspace.settings or {})
    except PydanticValidationError as e:
        raise TracecatValidationError(
            "The source workspace's settings are malformed"
        ) from e
    # Require an explicit provider rather than for_workspace's GitHub fallback,
    # so the pinned binding names the provider that will actually be used.
    if settings.git_provider is None or not settings.git_repo_url:
        raise TracecatValidationError(
            "The source workspace must have both a Git provider and a Git "
            "repository URL configured"
        )
    return ManagedSource(
        workspace_id=workspace.id,
        organization_id=workspace.organization_id,
        git_provider=settings.git_provider,
        git_repo_url=settings.git_repo_url,
    )


async def get_managed_source(session: AsyncSession) -> ManagedSource | None:
    """Return the configured source workspace, or ``None`` if none is set.

    Raises:
        ManagedSourceUnavailableError: The stored setting is malformed, or the
            workspace is gone, lost or has a malformed Git binding, or no longer
            has the binding that was pinned when the source was set.
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
        raise ManagedSourceUnavailableError(
            "The stored managed resources source setting is malformed"
        ) from e
    try:
        source = await load_managed_source(session, pinned.workspace_id)
    except (TracecatNotFoundError, TracecatValidationError) as e:
        raise ManagedSourceUnavailableError(
            f"The managed resources source workspace is no longer valid: {e}"
        ) from e
    if ManagedSourceSetting.pin(source) != pinned:
        raise ManagedSourceUnavailableError(
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


async def list_source_tags(
    session: AsyncSession,
    actor: PlatformRole,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[GitTagInfo], str | None]:
    """List one page of the source repository's tags, newest first.

    This is the only bridge from a platform superuser, who has no organization
    context, to the source organization's VCS credentials. Credentials come
    from :class:`WorkspaceSyncService` under :func:`source_repository_role`.

    The provider and repository URL are the ones :func:`get_managed_source`
    checked against the pin. They are never re-read from workspace settings,
    so a binding changed after that check cannot redirect the read.

    Args:
        session: A database session that can read across organizations.
        actor: The platform superuser making the request.
        limit: Maximum tags per page.
        cursor: An opaque cursor from a previous page, or ``None``.

    Raises:
        ManagedSourceUnavailableError: No source is configured, or the
            configured one can no longer be used.
    """
    source = await get_managed_source(session)
    if source is None:
        raise ManagedSourceUnavailableError(
            "Managed resources source workspace is not configured"
        )
    logger.info(
        "Using managed resources source repository",
        actor_id=actor.user_id,
        source_workspace_id=source.workspace_id,
        source_organization_id=source.organization_id,
    )
    service = WorkspaceSyncService(
        session, source_repository_role(source), provider=source.git_provider
    )
    url = parse_workspace_git_url(source.git_provider, source.git_repo_url)
    return await service.list_tags(limit=limit, cursor=cursor, url=url)
