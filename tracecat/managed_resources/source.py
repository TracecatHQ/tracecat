"""The source workspace that managed resources are published from.

Tracecat engineers publish a curated package from exactly one source workspace
to that workspace's Git sync repository. Platform superusers later roll tags of
that repository out to workspaces in other organizations. This module names the
source workspace and reads its repository with the source organization's VCS
credentials on behalf of a platform superuser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
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
from tracecat.git.types import GitUrl
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.logger import logger
from tracecat.managed_resources.source_setting import (
    MANAGED_RESOURCES_SOURCE_KEY,
    ManagedSourceSetting,
)
from tracecat.registry.repositories.schemas import GitCommitInfo, GitTagInfo
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    WorkspacePackageManifest,
    workspace_manifest_from_json,
)
from tracecat.workspace_sync.service import (
    WorkspaceSyncService,
    parse_workspace_git_url,
)
from tracecat.workspace_sync.transport import (
    VcsSyncTransport,
    VcsTransportFactory,
    VcsTreeSnapshot,
)

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


def pin_source(source: ManagedSource) -> ManagedSourceSetting:
    """Snapshot a validated source for storage."""
    return ManagedSourceSetting(
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
    if pin_source(source) != pinned:
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
    service, url = source_sync_service(session, source)
    return await service.list_tags(limit=limit, cursor=cursor, url=url)


def source_sync_service(
    session: AsyncSession,
    source: ManagedSource,
    *,
    transport_factory: VcsTransportFactory | None = None,
) -> tuple[WorkspaceSyncService, GitUrl]:
    """Return a sync service and URL that read the pinned source repository."""
    service = WorkspaceSyncService(
        session,
        source_repository_role(source),
        provider=source.git_provider,
        transport_factory=transport_factory,
    )
    return service, parse_workspace_git_url(source.git_provider, source.git_repo_url)


async def require_managed_source(session: AsyncSession) -> ManagedSource:
    """Return the configured source, or raise if none is configured.

    Raises:
        ManagedSourceUnavailableError: No usable source is configured.
    """
    source = await get_managed_source(session)
    if source is None:
        raise ManagedSourceUnavailableError(
            "Managed resources source workspace is not configured"
        )
    return source


async def resolve_source_tag(
    session: AsyncSession,
    source: ManagedSource,
    tag: str,
    *,
    transport_factory: VcsTransportFactory | None = None,
) -> GitTagInfo | None:
    """Find ``tag`` in the source repository by paging through its tags."""
    service, url = source_sync_service(
        session, source, transport_factory=transport_factory
    )
    cursor: str | None = None
    while True:
        tags, cursor = await service.list_tags(limit=100, cursor=cursor, url=url)
        if found := next((info for info in tags if info.name == tag), None):
            return found
        if cursor is None:
            return None


async def read_source_commit(
    session: AsyncSession,
    source: ManagedSource,
    commit_sha: str,
    *,
    transport_factory: VcsTransportFactory | None = None,
) -> VcsTreeSnapshot:
    """Read the source repository's sync files at one commit."""
    service, url = source_sync_service(
        session, source, transport_factory=transport_factory
    )
    transport = service.transport()
    return await transport.read_files(url=url, ref=commit_sha, ref_kind="commit")


RELEASE_TAG_PATTERN = re.compile(r"^v\d+\.\d+\.\d+$")
"""Release tags cut from ``/admin`` are ``vMAJOR.MINOR.PATCH``."""


async def cut_source_release(
    session: AsyncSession,
    source: ManagedSource,
    tag: str,
    *,
    transport_factory: VcsTransportFactory | None = None,
) -> GitTagInfo:
    """Tag the HEAD of the source repository's base branch as ``tag``.

    The base branch is the one the source binding names, or the
    repository's default branch.

    Raises:
        TracecatValidationError: ``tag`` is not ``vX.Y.Z`` or already exists.
        VcsProviderError: The provider refused the request.
    """
    if not RELEASE_TAG_PATTERN.fullmatch(tag):
        raise TracecatValidationError("Release tags must look like v1.2.3")
    if await resolve_source_tag(
        session, source, tag, transport_factory=transport_factory
    ):
        raise TracecatValidationError(f"Tag {tag!r} already exists")
    service, url = source_sync_service(
        session, source, transport_factory=transport_factory
    )
    transport = service.transport()
    _branch, head = await _base_branch_head(transport, url)
    await transport.create_tag(url=url, tag=tag, sha=head.sha)
    logger.info(
        "Cut managed resources release",
        tag=tag,
        commit_sha=head.sha,
        source_workspace_id=source.workspace_id,
    )
    return GitTagInfo(name=tag, commit_sha=head.sha, date=head.date)


async def _base_branch_head(
    transport: VcsSyncTransport, url: GitUrl
) -> tuple[str, GitCommitInfo]:
    """Return the source repository's base branch and its latest commit.

    The base branch is the one the source binding names, or the
    repository's default branch.

    Raises:
        TracecatValidationError: There is no default branch, or it is empty.
    """
    branch = url.ref
    if not branch:
        branches = await transport.list_branches(url=url, limit=10000)
        branch = next((b.name for b in branches if b.is_default), None)
    if not branch:
        raise TracecatValidationError("The source repository has no default branch")
    commits = await transport.list_commits(url=url, branch=branch, limit=1)
    if not commits:
        raise TracecatValidationError(f"Branch {branch!r} has no commits")
    return branch, commits[0]


@dataclass(frozen=True, slots=True)
class ReleaseCandidate:
    """What cutting a release now would tag."""

    branch: str
    commit: GitCommitInfo
    existing_tags: list[str]
    """Tags that already point at this commit."""
    package: WorkspacePackageManifest | None
    """The package metadata at this commit, if a package was published."""


async def source_release_candidate(
    session: AsyncSession,
    source: ManagedSource,
    *,
    transport_factory: VcsTransportFactory | None = None,
) -> ReleaseCandidate:
    """Describe the commit a new release would tag, and the package in it.

    Raises:
        TracecatValidationError: The base branch is missing or empty.
        VcsProviderError: The provider refused the request.
    """
    service, url = source_sync_service(
        session, source, transport_factory=transport_factory
    )
    transport = service.transport()
    branch, head = await _base_branch_head(transport, url)
    existing: list[str] = []
    cursor: str | None = None
    while True:
        tags, cursor = await service.list_tags(limit=100, cursor=cursor, url=url)
        existing.extend(info.name for info in tags if info.commit_sha == head.sha)
        if cursor is None:
            break
    tree = await transport.read_files(url=url, ref=head.sha, ref_kind="commit")
    package: WorkspacePackageManifest | None = None
    if manifest := tree.files.get(MANIFEST_FILENAME):
        try:
            package = workspace_manifest_from_json(manifest).package
        except ValueError:
            package = None
    return ReleaseCandidate(
        branch=branch, commit=head, existing_tags=existing, package=package
    )
