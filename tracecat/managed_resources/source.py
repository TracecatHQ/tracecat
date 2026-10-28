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

import orjson
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

MANAGED_RESOURCES_SOURCE_WORKSPACE_ID_KEY = "managed_resources_source_workspace_id"
"""Platform setting key holding the source workspace ID as a JSON string."""

SOURCE_REPOSITORY_SCOPES: frozenset[str] = frozenset({"workspace_sync:sync"})
"""The only scope granted when reading the source repository.

It is the scope the org VCS credential getters and the workspace sync read
methods require, and it grants nothing else.
"""


class ManagedSourceNotConfiguredError(TracecatSettingsError):
    """No managed resources source workspace has been configured."""


@dataclass(frozen=True, slots=True)
class ManagedSource:
    """A validated managed resources source workspace."""

    workspace_id: WorkspaceID
    organization_id: OrganizationID
    git_provider: VcsProvider
    git_repo_url: str


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
        TracecatNotFoundError: The configured workspace no longer exists.
        TracecatValidationError: The configured workspace lost its Git binding.
    """
    raw = await session.scalar(
        select(PlatformSetting.value).where(
            PlatformSetting.key == MANAGED_RESOURCES_SOURCE_WORKSPACE_ID_KEY
        )
    )
    if raw is None:
        return None
    workspace_id = uuid.UUID(orjson.loads(raw))
    return await load_managed_source(session, workspace_id)


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
