from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.credentials import RoleACL
from tracecat.auth.dependencies import OrgUserOrServiceRole, OrgUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.schemas import RegistryRepository
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionRead
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import (
    CUSTOM_REPOSITORY_ORIGIN,
    DEFAULT_REGISTRY_ORIGIN,
)
from tracecat.registry.repositories.models import (
    RegistryRepositoryCreate,
    RegistryRepositoryRead,
    RegistryRepositoryReadMinimal,
    RegistryRepositoryUpdate,
)
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.repository import ensure_base_repository
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError

router = APIRouter(prefix="/registry/repos", tags=["registry-repositories"])

# Controls


@router.post("/sync", status_code=status.HTTP_204_NO_CONTENT)
async def sync_registry_repositories(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=True,
        require_workspace=False,
    ),
    session: AsyncDBSession,
    origins: list[str] | None = Query(
        None,
        description="Origins to sync. If no origins provided, all repositories will be synced.",
    ),
) -> None:
    """Load actions from all registry repositories."""
    repos_service = RegistryReposService(session, role=role)
    # Check if the base registry repository already exists
    await ensure_base_repository(session=session, role=role)
    if origins is None:
        repos = await repos_service.list_repositories()
    else:
        # If origins are provided, only sync those repositories
        repos: list[RegistryRepository] = []
        for origin in origins:
            if (repo := await repos_service.get_repository(origin)) is None:
                # If it doesn't exist, create the base registry repository
                repo = await repos_service.create_repository(
                    RegistryRepositoryCreate(origin=origin)
                )
                logger.info("Created repository", origin=origin)
            else:
                logger.info(
                    "Repository already exists, skipping creation", origin=origin
                )
            repos.append(repo)

    actions_service = RegistryActionsService(session, role=role)
    try:
        await actions_service.sync_actions(repos)
    except TracecatNotFoundError as e:
        logger.error("Error while syncing repository", exc=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error("Unexpected error while syncing repository", exc=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.get("")
async def list_registry_repositories(
    role: OrgUserRole, session: AsyncDBSession
) -> list[RegistryRepositoryReadMinimal]:
    """List all registry repositories."""
    service = RegistryReposService(session, role)
    repositories = await service.list_repositories()
    logger.info(
        "Listing registry repositories",
        repositories=[repo.origin for repo in repositories],
    )
    return [RegistryRepositoryReadMinimal(origin=repo.origin) for repo in repositories]


@router.get("/{origin:path}", response_model=RegistryRepositoryRead)
async def get_registry_repository(
    role: OrgUserRole, session: AsyncDBSession, origin: str
) -> RegistryRepositoryRead:
    """Get a specific registry repository by origin."""
    service = RegistryReposService(session, role)
    repository = await service.get_repository(origin)
    logger.info("Getting registry repository", origin=origin, repository=repository)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        )
    return RegistryRepositoryRead(
        origin=repository.origin,
        actions=[
            RegistryActionRead.model_validate(action, from_attributes=True)
            for action in repository.actions
        ],
    )


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=RegistryRepositoryRead
)
async def create_registry_repository(
    role: OrgUserOrServiceRole,
    session: AsyncDBSession,
    params: RegistryRepositoryCreate,
) -> RegistryRepositoryRead:
    """Create a new registry repository."""
    service = RegistryReposService(session, role=role)
    try:
        logger.info("Creating registry", params=params)
        created_repository = await service.create_repository(params)
        return RegistryRepositoryRead(
            actions=[
                RegistryActionRead.model_validate(action, from_attributes=True)
                for action in created_repository.actions
            ]
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.patch("/{origin:path}", response_model=RegistryRepositoryRead)
async def update_registry_repository(
    role: OrgUserOrServiceRole,
    session: AsyncDBSession,
    origin: str,
    params: RegistryRepositoryUpdate,
) -> RegistryRepositoryRead:
    """Update an existing registry repository."""
    service = RegistryReposService(session, role)
    repository = await service.get_repository(origin)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        )
    for key, value in params.model_dump(exclude_unset=True).items():
        setattr(repository, key, value)
    updated_repository = await service.update_repository(repository)
    return RegistryRepositoryRead(
        origin=updated_repository.origin,
        actions=[action.to_read_model() for action in updated_repository.actions],
    )


@router.delete("/{origin:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_registry_repository(
    role: OrgUserOrServiceRole, session: AsyncDBSession, origin: str
):
    """Delete a registry repository."""
    logger.info("Deleting registry repository", origin=origin)
    if origin == DEFAULT_REGISTRY_ORIGIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot delete the base Tracecat repository.",
        )
    elif origin == CUSTOM_REPOSITORY_ORIGIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot delete the custom repository.",
        )
    service = RegistryReposService(session, role)
    repository = await service.get_repository(origin)
    if repository is None:
        logger.error("Registry repository not found", origin=origin)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        )
    await service.delete_repository(repository)
