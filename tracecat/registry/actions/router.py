from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from tracecat.auth.credentials import RoleACL
from tracecat.concurrency import GatheringTaskGroup
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.registry.actions.models import (
    RegistryActionCreate,
    RegistryActionRead,
    RegistryActionUpdate,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN, REGISTRY_ACTIONS_PATH
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import RegistryError

router = APIRouter(prefix=REGISTRY_ACTIONS_PATH, tags=["registry-actions"])


@router.get("")
async def list_registry_actions(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
) -> list[RegistryActionRead]:
    """List all actions in a registry."""
    service = RegistryActionsService(session, role)
    actions = await service.list_actions()

    async with GatheringTaskGroup[RegistryActionRead]() as tg:
        for action in actions:
            tg.create_task(service.read_action_with_implicit_secrets(action))
    return tg.results()


@router.get(
    "/{action_name}",
    response_model=RegistryActionRead,
    response_model_exclude_unset=True,
)
async def get_registry_action(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    action_name: str,
) -> RegistryActionRead:
    """Get a specific registry action."""
    service = RegistryActionsService(session, role)
    try:
        action = await service.get_action(action_name=action_name)
        return await service.read_action_with_implicit_secrets(action)
    except RegistryError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_registry_action(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
    params: RegistryActionCreate,
) -> RegistryActionRead:
    """Create a new registry action."""
    service = RegistryActionsService(session, role)
    try:
        action = await service.create_action(params)
        return await service.read_action_with_implicit_secrets(action)
    except IntegrityError as e:
        msg = str(e)
        if "duplicate key value" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Action {params.namespace}.{params.name} already exists.",
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg
        ) from e


@router.patch("/{action_name}", status_code=status.HTTP_204_NO_CONTENT)
async def update_registry_action(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
    params: RegistryActionUpdate,
    action_name: str,
) -> None:
    """Update a custom registry action."""
    service = RegistryActionsService(session, role)
    try:
        action = await service.get_action(action_name=action_name)
        await service.update_action(action, params)
    except RegistryError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.delete("/{action_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_registry_action(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
    action_name: str,
) -> None:
    """Delete a template action."""
    service = RegistryActionsService(session, role)
    try:
        action = await service.get_action(action_name=action_name)
    except RegistryError as e:
        logger.error("Error getting action", action_name=action_name, error=e)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    if action.origin == DEFAULT_REGISTRY_ORIGIN:
        logger.warning(
            "Attempted to delete default action",
            action_name=action_name,
            origin=action.origin,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot delete default actions. Please delete the action from your custom registry if you want to remove it.",
        )
    # Delete the action as it's not a base action
    await service.delete_action(action)
