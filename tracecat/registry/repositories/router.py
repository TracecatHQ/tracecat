from fastapi import APIRouter, HTTPException, status
from tracecat_registry import REGISTRY_VERSION

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
    base_version = REGISTRY_VERSION
    # Check if the base registry repository already exists
    if await repos_service.get_repository(base_version) is None:
        # If it doesn't exist, create the base registry repository
        await repos_service.create_repository(
            RegistryRepositoryCreate(
                version=base_version,
                origin=DEFAULT_REGISTRY_ORIGIN,
            )
        )
        logger.info("Created base registry repository", version=base_version)
    else:
        logger.info("Base registry repository already exists", version=base_version)
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
        "Listing repositories", repositories=[repo.version for repo in repositories]
    )
    return [
        RegistryRepositoryReadMinimal(version=repo.version, origin=repo.origin)
        for repo in repositories
    ]


@router.get("/{version}", response_model=RegistryRepositoryRead)
async def get_registry_repository(
    version: str, role: OrgUserRole, session: AsyncDBSession
) -> RegistryRepositoryRead:
    """Get a specific registry repository by version."""
    service = RegistryReposService(session, role)
    repository = await service.get_repository(version)
    logger.info("Getting registry", version=version, repository=repository)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry not found",
        )
    return RegistryRepositoryRead(
        version=repository.version,
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
    "/{version}",
    response_model=RegistryRepositoryRead,
)
async def update_registry_repository(
    role: OrgUserOrServiceRole,
    session: AsyncDBSession,
    version: str,
    params: RegistryRepositoryUpdate,
) -> RegistryRepositoryRead:
    """Update an existing registry repository."""
    service = RegistryReposService(session, role)
    repository = await service.get_repository(version)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry not found",
        )
    for key, value in params.model_dump(exclude_unset=True).items():
        setattr(repository, key, value)
    updated_repository = await service.update_repository(repository)
    return RegistryRepositoryRead(
        origin=updated_repository.origin,
        actions=[action.to_read_model() for action in updated_repository.actions],
    )


@router.delete("/{version}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_registry_repository(
    version: str, role: OrgUserOrServiceRole, session: AsyncDBSession
):
    """Delete a registry repository."""
    service = RegistryReposService(session, role)
    repository = await service.get_repository(version)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registry not found",
        )
    await service.delete_repository(repository)
