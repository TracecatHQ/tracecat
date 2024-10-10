from fastapi import APIRouter, HTTPException, status

from tracecat.auth.dependencies import OrgUserOrServiceRole, OrgUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionRead
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.repositories.models import (
    RegistryRepositoryCreate,
    RegistryRepositoryRead,
    RegistryRepositoryReadMinimal,
    RegistryRepositoryUpdate,
)
from tracecat.registry.repositories.service import RegistryReposService

router = APIRouter(prefix="/registry/repos", tags=["registry-repositories"])

# Controls


@router.post("/sync", status_code=status.HTTP_204_NO_CONTENT)
async def sync_registry_repositories(
    role: OrgUserOrServiceRole, session: AsyncDBSession
) -> None:
    """Load actions from a registry repository."""
    repos_service = RegistryReposService(session, role=role)
    # Check if the base registry repository already exists
    origin = DEFAULT_REGISTRY_ORIGIN
    if await repos_service.get_repository(origin) is None:
        # If it doesn't exist, create the base registry repository
        await repos_service.create_repository(RegistryRepositoryCreate(origin=origin))
        logger.info("Created base registry repository", origin=origin)
    else:
        logger.info("Base registry repository already exists", origin=origin)
    repos = await repos_service.list_repositories()

    actions_service = RegistryActionsService(session, role=role)
    await actions_service.sync_actions(repos)


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


@router.get("/{origin}", response_model=RegistryRepositoryRead)
async def get_registry_repository(
    origin: str, role: OrgUserRole, session: AsyncDBSession
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


@router.patch(
    "/{origin}",
    response_model=RegistryRepositoryRead,
)
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


@router.delete("/{origin}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_registry_repository(
    origin: str, role: OrgUserOrServiceRole, session: AsyncDBSession
):
    """Delete a registry repository."""
    service = RegistryReposService(session, role)
    repository = await service.get_repository(origin)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry repository not found",
        )
    await service.delete_repository(repository)
