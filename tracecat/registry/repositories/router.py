from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import RegistryRepository
from tracecat.exceptions import (
    RegistryActionValidationError,
    RegistryError,
    TracecatCredentialsNotFoundError,
    TracecatValidationError,
)
from tracecat.git.utils import list_git_commits, parse_git_url
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.common import ensure_org_repositories
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN, REGISTRY_REPOS_PATH
from tracecat.registry.repositories.schemas import (
    GitCommitInfo,
    RegistryRepositoryCreate,
    RegistryRepositoryErrorDetail,
    RegistryRepositoryRead,
    RegistryRepositoryReadMinimal,
    RegistryRepositorySync,
    RegistryRepositoryUpdate,
    RegistrySyncResponse,
    RegistryVersionPromoteResponse,
    RegistryVersionRead,
)
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.versions.schemas import VersionDiff
from tracecat.registry.versions.service import RegistryVersionsService
from tracecat.settings.service import get_setting
from tracecat.ssh import ssh_context
from tracecat.tiers.entitlements import Entitlement, check_entitlement

router = APIRouter(prefix=REGISTRY_REPOS_PATH, tags=["registry-repositories"])

# Controls


@router.post(
    "/{repository_id}/sync",
    response_model=RegistrySyncResponse,
    responses={
        422: {
            "model": RegistryRepositoryErrorDetail,
            "description": "Registry sync validation error",
        },
        404: {"description": "Registry repository not found"},
        400: {"description": "Cannot sync repository"},
    },
)
@require_scope("org:registry:manage")
async def sync_registry_repository(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: uuid.UUID,
    sync_params: RegistryRepositorySync | None = None,
) -> RegistrySyncResponse:
    """Sync an org-scoped registry repository.

    For platform registries (base registry), use the admin API at /admin/registry/sync.

    Args:
        repository_id: The ID of the repository to sync
        sync_params: Optional sync parameters, including target commit SHA and force flag

    Raises:
        422: If there is an error syncing the repository (validation error)
        404: If the repository is not found
        400: If there is an error syncing the repository
    """
    repos_service = RegistryReposService(session, role)
    try:
        repo = await repos_service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        logger.error("Registry repository not found", repository_id=repository_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e
    actions_service = RegistryActionsService(session, role)
    last_synced_at = datetime.now(UTC)
    target_commit_sha = sync_params.target_commit_sha if sync_params else None
    force = sync_params.force if sync_params else False

    # For git+ssh repos, we need SSH context for tarball building
    is_git_ssh = repo.origin.startswith("git+ssh://")
    git_repo_package_name: str | None = None
    if is_git_ssh:
        git_repo_package_name = await get_setting("git_repo_package_name", role=role)

    # If force=True, delete the current version before syncing
    if force and repo.current_version_id is not None:
        versions_service = RegistryVersionsService(session, role)
        current_version = await versions_service.get_version(repo.current_version_id)
        if current_version:
            logger.info(
                "Force sync: deleting current version",
                repository_id=str(repository_id),
                version_id=str(current_version.id),
                version=current_version.version,
            )
            await versions_service.delete_version(current_version, commit=False)
            await session.flush()

    version: str | None = None
    commit_sha: str | None = None
    actions_count: int | None = None

    try:
        if is_git_ssh:
            # Get SSH context for git operations
            allowed_domains_setting = await get_setting(
                "git_allowed_domains", role=role
            )
            allowed_domains = allowed_domains_setting or {"github.com"}
            git_url = parse_git_url(repo.origin, allowed_domains=allowed_domains)

            async with ssh_context(
                role=role, git_url=git_url, session=session
            ) as ssh_env:
                # Sync with SSH env for tarball building
                (
                    commit_sha,
                    version,
                ) = await actions_service.sync_actions_from_repository(
                    repo,
                    target_commit_sha=target_commit_sha,
                    git_repo_package_name=git_repo_package_name,
                    ssh_env=ssh_env,
                )
        else:
            # Sync without SSH (built-in registry)
            (
                commit_sha,
                version,
            ) = await actions_service.sync_actions_from_repository(
                repo,
                target_commit_sha=target_commit_sha,
                git_repo_package_name=git_repo_package_name,
            )
        logger.info(
            "Synced repository",
            origin=repo.origin,
            commit_sha=commit_sha,
            version=version,
            target_commit_sha=target_commit_sha,
            last_synced_at=last_synced_at,
            force=force,
        )

        session.expire(repo)
        # Update the registry repository table
        await repos_service.update_repository(
            repo,
            RegistryRepositoryUpdate(
                last_synced_at=last_synced_at, commit_sha=commit_sha
            ),
        )
        logger.info("Updated repository", origin=repo.origin)

        # Get action count from registry index (v2 sync populates index, not RegistryAction)
        index_actions = await actions_service.list_actions_from_index_by_repository(
            repo.id
        )
        actions_count = len(index_actions)

        return RegistrySyncResponse(
            success=True,
            repository_id=repo.id,
            origin=repo.origin,
            version=version,
            commit_sha=commit_sha,
            actions_count=actions_count,
            forced=force,
        )

    except RegistryError as e:
        logger.warning("Cannot sync repository", exc=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except TracecatCredentialsNotFoundError as e:
        logger.warning("Credentials not found", exc=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except RegistryActionValidationError as e:
        logger.warning("Validation errors while syncing repository", exc=e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=RegistryRepositoryErrorDetail(
                id=str(repo.id),
                origin=repo.origin,
                message=str(e),
                errors=e.detail,
            ).model_dump(),
        ) from e
    except Exception as e:
        logger.error("Unexpected error while syncing repository", exc=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error while syncing repository {repo.origin!r}: {e}",
        ) from e


@router.get("/{repository_id}/versions", response_model=list[RegistryVersionRead])
@require_scope("org:registry:read")
async def list_repository_versions(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: uuid.UUID,
) -> list[RegistryVersionRead]:
    """List all versions for an org-scoped registry repository.

    For platform registry versions, use the admin API at /admin/registry/versions.
    """
    repos_service = RegistryReposService(session, role)
    try:
        await repos_service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        logger.error("Registry repository not found", repository_id=repository_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e

    versions_service = RegistryVersionsService(session, role)
    versions = await versions_service.list_versions(repository_id=repository_id)
    return [RegistryVersionRead.model_validate(v) for v in versions]


@router.get("", response_model=list[RegistryRepositoryReadMinimal])
@require_scope("org:registry:read")
async def list_registry_repositories(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
) -> list[RegistryRepositoryReadMinimal]:
    """List org-scoped registry repositories.

    This endpoint ensures org-scoped repositories (local, custom git) exist
    before returning the list. Platform registry is handled separately at startup.

    For platform registries (base registry), use the admin API at /admin/registry/repos.
    """
    # Ensure org-scoped repos exist before listing
    await ensure_org_repositories(session, role)

    stmt = select(
        RegistryRepository.id,
        RegistryRepository.origin,
        RegistryRepository.last_synced_at,
        RegistryRepository.commit_sha,
        RegistryRepository.current_version_id,
    ).where(RegistryRepository.organization_id == role.organization_id)

    result = await session.execute(stmt)
    rows = result.tuples().all()

    repositories = [
        RegistryRepositoryReadMinimal(
            id=id,
            origin=origin,
            last_synced_at=last_synced_at,
            commit_sha=commit_sha,
            current_version_id=current_version_id,
        )
        for id, origin, last_synced_at, commit_sha, current_version_id in rows
    ]

    logger.info(
        "Listing registry repositories",
        repositories=[repo.origin for repo in repositories],
        count=len(repositories),
    )
    return repositories


@router.get("/{repository_id}", response_model=RegistryRepositoryRead)
@require_scope("org:registry:read")
async def get_registry_repository(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: uuid.UUID,
) -> RegistryRepositoryRead:
    """Get a specific org-scoped registry repository by ID.

    For platform registries, use the admin API at /admin/registry/repos/{repository_id}.
    """
    repos_service = RegistryReposService(session, role)
    actions_service = RegistryActionsService(session, role)
    try:
        repository = await repos_service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        logger.error("Error getting registry repository", exc=e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e
    actions = await actions_service.list_actions_from_index_by_repository(repository_id)
    return RegistryRepositoryRead(
        id=repository.id,
        origin=repository.origin,
        last_synced_at=repository.last_synced_at,
        commit_sha=repository.commit_sha,
        current_version_id=repository.current_version_id,
        actions=actions,
    )


@router.get("/{repository_id}/commits", response_model=list[GitCommitInfo])
@require_scope("org:registry:read")
async def list_repository_commits(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: uuid.UUID,
    branch: str = "main",
    limit: int = Query(
        default=config.TRACECAT__LIMIT_COMMITS_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
) -> list[GitCommitInfo]:
    """List commits from a specific registry repository."""
    repos_service = RegistryReposService(session, role)

    try:
        repo = await repos_service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        logger.error("Registry repository not found", repository_id=repository_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e

    # Only support git+ssh repositories for commit listing
    if not repo.origin.startswith("git+ssh://"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Commit listing is only supported for git+ssh repositories",
        )

    try:
        # Parse git URL to get allowed domains
        allowed_domains_setting = await get_setting("git_allowed_domains", role=role)
        allowed_domains = allowed_domains_setting or {"github.com"}

        git_url = parse_git_url(repo.origin, allowed_domains=allowed_domains)

        # Get SSH context for git operations
        async with (
            get_async_session_context_manager() as db_session,
            ssh_context(role=role, git_url=git_url, session=db_session) as ssh_env,
        ):
            if ssh_env is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No SSH key found for git operations",
                )

            # List commits from the repository
            commits = await list_git_commits(
                repo.origin,
                env=ssh_env,
                branch=branch,
                limit=min(limit, 100),  # Cap at 100 commits max
            )

            logger.info(
                "Listed repository commits",
                repository_id=repository_id,
                origin=repo.origin,
                branch=branch,
                count=len(commits),
            )

            return commits

    except ValueError as e:
        logger.error("Invalid git URL", origin=repo.origin, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid git repository URL: {str(e)}",
        ) from e
    except RuntimeError as e:
        logger.error("Git operation failed", origin=repo.origin, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to list commits: {str(e)}",
        ) from e
    except Exception as e:
        logger.error("Unexpected error listing commits", exc=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while listing commits",
        ) from e


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=RegistryRepositoryRead
)
@require_scope("org:registry:manage")
async def create_registry_repository(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    params: RegistryRepositoryCreate,
) -> RegistryRepositoryRead:
    """Create a new registry repository."""
    # Check entitlement for custom registry (non-system repositories)
    if params.origin != DEFAULT_REGISTRY_ORIGIN:
        await check_entitlement(session, role, Entitlement.CUSTOM_REGISTRY)

    service = RegistryReposService(session, role=role)
    try:
        created_repository = await service.create_repository(params)
    except IntegrityError as e:
        msg = str(e)
        logger.error("Error creating registry repository", exc=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=msg,
        ) from e
    except TracecatValidationError as e:
        logger.error("Error creating registry repository", exc=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    # New repository has no synced actions yet
    return RegistryRepositoryRead(
        id=created_repository.id,
        origin=created_repository.origin,
        last_synced_at=created_repository.last_synced_at,
        commit_sha=created_repository.commit_sha,
        current_version_id=created_repository.current_version_id,
        actions=[],
    )


@router.patch("/{repository_id}", response_model=RegistryRepositoryRead)
@require_scope("org:registry:manage")
async def update_registry_repository(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: uuid.UUID,
    params: RegistryRepositoryUpdate,
) -> RegistryRepositoryRead:
    """Update an existing registry repository."""
    repos_service = RegistryReposService(session, role)
    actions_service = RegistryActionsService(session, role)
    try:
        repository = await repos_service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        logger.error("Registry repository not found", repository_id=repository_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e
    updated_repository = await repos_service.update_repository(repository, params)
    actions = await actions_service.list_actions_from_index_by_repository(repository_id)
    return RegistryRepositoryRead(
        id=updated_repository.id,
        origin=updated_repository.origin,
        last_synced_at=updated_repository.last_synced_at,
        commit_sha=updated_repository.commit_sha,
        current_version_id=updated_repository.current_version_id,
        actions=actions,
    )


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:registry:manage")
async def delete_registry_repository(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: uuid.UUID,
) -> None:
    """Delete a registry repository."""
    service = RegistryReposService(session, role)
    try:
        repository = await service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        logger.error("Registry repository not found", repository_id=repository_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e
    logger.info("Deleting registry repository", repository_id=repository_id)
    if repository.origin == DEFAULT_REGISTRY_ORIGIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"The {repository.origin!r} repository cannot be deleted.",
        )
    await service.delete_repository(repository)


@router.post(
    "/{repository_id}/versions/{version_id}/promote",
    response_model=RegistryVersionPromoteResponse,
)
@require_scope("org:registry:manage")
async def promote_registry_version(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: uuid.UUID,
    version_id: uuid.UUID,
) -> RegistryVersionPromoteResponse:
    """Promote a specific version to be the current version of an org-scoped repository.

    This endpoint allows administrators to manually promote or rollback to a
    specific registry version, overriding the auto-promotion that happens during sync.

    For platform registries, use the admin API at
    /admin/registry/{repository_id}/versions/{version_id}/promote.

    Args:
        repository_id: The ID of the repository
        version_id: The ID of the version to promote

    Returns:
        RegistryVersionPromoteResponse with previous and current version info

    Raises:
        404: If repository or version not found
        400: If version doesn't belong to repository or has no tarball
    """
    repos_service = RegistryReposService(session, role)
    try:
        repository = await repos_service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        logger.error("Registry repository not found", repository_id=repository_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e

    previous_version_id = repository.current_version_id

    try:
        updated_repo = await repos_service.promote_version(repository, version_id)
    except RegistryError as e:
        logger.warning("Cannot promote version", exc=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    # Get the version string for the response
    versions_service = RegistryVersionsService(session, role)
    version = await versions_service.get_version(version_id)
    version_string = version.version if version else str(version_id)

    return RegistryVersionPromoteResponse(
        repository_id=updated_repo.id,
        origin=updated_repo.origin,
        previous_version_id=previous_version_id,
        current_version_id=version_id,
        version=version_string,
    )


@router.delete(
    "/{repository_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("org:registry:manage")
async def delete_registry_version(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: uuid.UUID,
    version_id: uuid.UUID,
) -> None:
    """Delete a specific registry version.

    Safety checks:
    - Cannot delete the currently promoted version
    - Cannot delete versions referenced by published workflow definitions

    For platform registries, use the admin API.
    """
    repos_service = RegistryReposService(session, role)
    versions_service = RegistryVersionsService(session, role)

    try:
        repository = await repos_service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e

    # Check entitlement for custom registry (non-default repositories)
    if repository.origin != DEFAULT_REGISTRY_ORIGIN:
        await check_entitlement(session, role, Entitlement.CUSTOM_REGISTRY)

    version = await versions_service.get_version(version_id)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry version not found",
        )

    if version.repository_id != repository_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Version does not belong to this repository",
        )

    # Validate that the version can be deleted
    try:
        await repos_service.validate_version_deletion(repository, version)
    except RegistryError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    await versions_service.delete_version(version)
    logger.info(
        "Deleted registry version",
        repository_id=str(repository_id),
        version_id=str(version_id),
        version=version.version,
    )


@router.get(
    "/{repository_id}/versions/{version_id}/diff",
    response_model=VersionDiff,
)
@require_scope("org:registry:read")
async def compare_registry_versions(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: uuid.UUID,
    version_id: uuid.UUID,
    compare_to: uuid.UUID,
) -> VersionDiff:
    """Compare two registry versions and return the diff.

    Args:
        repository_id: The repository ID
        version_id: The base version ID (typically older)
        compare_to: The version ID to compare against (typically newer)

    Returns:
        VersionDiff with added, removed, and modified actions
    """
    repos_service = RegistryReposService(session, role)
    versions_service = RegistryVersionsService(session, role)

    try:
        await repos_service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e

    base_version = await versions_service.get_version(version_id)
    if base_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Base version not found",
        )

    compare_version = await versions_service.get_version(compare_to)
    if compare_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Compare version not found",
        )

    # Ensure both versions belong to the same repository
    if base_version.repository_id != repository_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Base version does not belong to this repository",
        )
    if compare_version.repository_id != repository_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Compare version does not belong to this repository",
        )

    return await versions_service.compare_versions(base_version, compare_version)


@router.get(
    "/{repository_id}/versions/{version_id}/previous",
    response_model=RegistryVersionRead | None,
)
@require_scope("org:registry:read")
async def get_previous_registry_version(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: uuid.UUID,
    version_id: uuid.UUID,
) -> RegistryVersionRead | None:
    """Get the previous version before the specified version.

    Useful for quick rollback UX - returns the version to rollback to.
    Returns null if there is no previous version.
    """
    repos_service = RegistryReposService(session, role)
    versions_service = RegistryVersionsService(session, role)

    try:
        await repos_service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e

    # Verify the version exists
    version = await versions_service.get_version(version_id)
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry version not found",
        )

    if version.repository_id != repository_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Version does not belong to this repository",
        )

    previous = await versions_service.get_previous_version(repository_id, version_id)
    if previous is None:
        return None

    return RegistryVersionRead.model_validate(previous)
