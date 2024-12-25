from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.executor.client import ExecutorClient
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionRead
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import (
    CUSTOM_REPOSITORY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
    REGISTRY_REPOS_PATH,
)
from tracecat.registry.repositories.models import (
    RegistryRepositoryCreate,
    RegistryRepositoryRead,
    RegistryRepositoryReadMinimal,
    RegistryRepositoryUpdate,
)
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import RegistryError

router = APIRouter(prefix=REGISTRY_REPOS_PATH, tags=["registry-repositories"])

# Controls


@router.post("/{repository_id}/sync", status_code=status.HTTP_204_NO_CONTENT)
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
) -> None:
    """Load actions from a specific registry repository."""
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
    try:
        # Update the registry actions table
        commit_sha = await actions_service.sync_actions_from_repository(repo)
        logger.info(
            "Synced repository",
            origin=repo.origin,
            commit_sha=commit_sha,
            last_synced_at=last_synced_at,
        )
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error while syncing repository {repo.origin!r}: {e}",
        ) from e
    except Exception as e:
        logger.error("Unexpected error while syncing repository", exc=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unexpected error while syncing repository {repo.origin!r}: {e}",
        ) from e


@router.post("/{repository_id}/sync-executor", status_code=status.HTTP_204_NO_CONTENT)
async def sync_executor_from_registry_repository(
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
    # # We might want to update the executor's view of the repository here
    # # (3) Update the executor's view of the repository
    rr_service = RegistryReposService(session, role)
    try:
        repo = await rr_service.get_repository_by_id(repository_id)
    except NoResultFound as e:
        logger.error("Registry repository not found", repository_id=repository_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        ) from e
    logger.info("Syncing executor", origin=repo.origin)
    client = ExecutorClient(role=role)
    try:
        await client.sync_executor(repository_id=repo.id)
    except RegistryError as e:
        logger.warning("Error syncing executor", exc=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error while syncing executor {repo.origin!r}: {e}",
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
    if repository.origin in (DEFAULT_REGISTRY_ORIGIN, CUSTOM_REPOSITORY_ORIGIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"The {repository.origin!r} repository cannot be deleted.",
        )
    await service.delete_repository(repository)
