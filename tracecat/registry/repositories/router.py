from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import AccessLevel, Role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.engine import get_async_session_context_manager
from tracecat.exceptions import (
    RegistryActionValidationError,
    RegistryError,
    TracecatCredentialsNotFoundError,
    TracecatValidationError,
)
from tracecat.feature_flags import FeatureFlag, is_feature_enabled
from tracecat.git.utils import list_git_commits, parse_git_url
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionRead
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.common import reload_registry
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN, REGISTRY_REPOS_PATH
from tracecat.registry.repositories.schemas import (
    GitCommitInfo,
    RegistryRepositoryCreate,
    RegistryRepositoryErrorDetail,
    RegistryRepositoryRead,
    RegistryRepositoryReadMinimal,
    RegistryRepositorySync,
    RegistryRepositoryUpdate,
)
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.settings.service import get_setting
from tracecat.ssh import ssh_context

router = APIRouter(prefix=REGISTRY_REPOS_PATH, tags=["registry-repositories"])

# Controls


@router.post("/reload", status_code=status.HTTP_204_NO_CONTENT)
async def reload_registry_repositories(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
) -> None:
    """Refresh all registry repositories."""
    await reload_registry(session, role)


@router.post(
    "/{repository_id}/sync",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        422: {
            "model": RegistryRepositoryErrorDetail,
            "description": "Registry sync validation error",
        },
        404: {"description": "Registry repository not found"},
        400: {"description": "Cannot sync repository"},
    },
)
async def sync_registry_repository(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
    repository_id: UUID4,
    sync_params: RegistryRepositorySync | None = None,
) -> None:
    """Load actions from a specific registry repository.

    Args:
        repository_id: The ID of the repository to sync
        sync_params: Optional sync parameters, including target commit SHA

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

    # Check if v2 sync is enabled via feature flag
    use_v2_sync = is_feature_enabled(FeatureFlag.REGISTRY_SYNC_V2)

    # For git+ssh repos, we need SSH context for wheel building
    is_git_ssh = repo.origin.startswith("git+ssh://")

    try:
        if use_v2_sync:
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
                    # V2 sync with SSH env for wheel building
                    (
                        commit_sha,
                        version,
                    ) = await actions_service.sync_actions_from_repository_v2(
                        repo, target_commit_sha=target_commit_sha, ssh_env=ssh_env
                    )
            else:
                # V2 sync without SSH (built-in registry)
                (
                    commit_sha,
                    version,
                ) = await actions_service.sync_actions_from_repository_v2(
                    repo, target_commit_sha=target_commit_sha
                )
            logger.info(
                "Synced repository (v2)",
                origin=repo.origin,
                commit_sha=commit_sha,
                version=version,
                target_commit_sha=target_commit_sha,
                last_synced_at=last_synced_at,
            )
        else:
            # V1 sync: updates RegistryAction table only
            commit_sha = await actions_service.sync_actions_from_repository(
                repo, target_commit_sha=target_commit_sha
            )
            logger.info(
                "Synced repository",
                origin=repo.origin,
                commit_sha=commit_sha,
                target_commit_sha=target_commit_sha,
                last_synced_at=last_synced_at,
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unexpected error while syncing repository {repo.origin!r}: {e}",
        ) from e


@router.get("")
async def list_registry_repositories(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
) -> list[RegistryRepositoryReadMinimal]:
    """List all registry repositories."""
    service = RegistryReposService(session, role)
    repositories = await service.list_repositories()
    logger.info(
        "Listing registry repositories",
        repositories=[repo.origin for repo in repositories],
    )
    return [
        RegistryRepositoryReadMinimal(
            id=repo.id,
            origin=repo.origin,
            last_synced_at=repo.last_synced_at,
            commit_sha=repo.commit_sha,
        )
        for repo in repositories
    ]


@router.get("/{repository_id}", response_model=RegistryRepositoryRead)
async def get_registry_repository(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: UUID4,
) -> RegistryRepositoryRead:
    """Get a specific registry repository by origin."""
    service = RegistryReposService(session, role)
    try:
        repository = await service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        logger.error("Error getting registry repository", exc=e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e
    return RegistryRepositoryRead(
        id=repository.id,
        origin=repository.origin,
        last_synced_at=repository.last_synced_at,
        commit_sha=repository.commit_sha,
        actions=[
            RegistryActionRead.model_validate(action, from_attributes=True)
            for action in repository.actions
        ],
    )


@router.get("/{repository_id}/commits", response_model=list[GitCommitInfo])
async def list_repository_commits(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    repository_id: UUID4,
    branch: str = "main",
    limit: int = 10,
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
async def create_registry_repository(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
    params: RegistryRepositoryCreate,
) -> RegistryRepositoryRead:
    """Create a new registry repository."""
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
    return RegistryRepositoryRead(
        id=created_repository.id,
        origin=created_repository.origin,
        last_synced_at=created_repository.last_synced_at,
        commit_sha=created_repository.commit_sha,
        actions=[
            RegistryActionRead.model_validate(action, from_attributes=True)
            for action in created_repository.actions
        ],
    )


@router.patch("/{repository_id}", response_model=RegistryRepositoryRead)
async def update_registry_repository(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
    repository_id: UUID4,
    params: RegistryRepositoryUpdate,
) -> RegistryRepositoryRead:
    """Update an existing registry repository."""
    service = RegistryReposService(session, role)
    try:
        repository = await service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        logger.error("Registry repository not found", repository_id=repository_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e
    updated_repository = await service.update_repository(repository, params)
    return RegistryRepositoryRead(
        id=updated_repository.id,
        origin=updated_repository.origin,
        last_synced_at=updated_repository.last_synced_at,
        commit_sha=updated_repository.commit_sha,
        actions=[
            RegistryActionRead.model_validate(action, from_attributes=True)
            for action in updated_repository.actions
        ],
    )


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_registry_repository(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
    repository_id: UUID4,
):
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
