from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError
from tracecat_registry import RegistrySecret

from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import RegistryError
from tracecat.logger import logger
from tracecat.registry.actions.schemas import (
    RegistryActionCreate,
    RegistryActionRead,
    RegistryActionReadMinimal,
    RegistryActionUpdate,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN, REGISTRY_ACTIONS_PATH

router = APIRouter(prefix=REGISTRY_ACTIONS_PATH, tags=["registry-actions"])


@router.get("")
@require_scope("workflow:read")
async def list_registry_actions(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
) -> list[RegistryActionReadMinimal]:
    """List all actions from registry index."""
    service = RegistryActionsService(session, role)
    index_entries = await service.list_actions_from_index()
    return [
        RegistryActionReadMinimal.from_index(entry, origin)
        for entry, origin in index_entries
    ]


@router.get(
    "/{action_name}",
    response_model=RegistryActionRead,
    response_model_exclude_unset=True,
)
@require_scope("workflow:read")
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
    result = await service.get_action_from_index(action_name)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action {action_name} not found",
        )

    manifest_action = result.manifest.actions.get(action_name)
    if not manifest_action:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action {action_name} not found in manifest",
        )

    # Aggregate secrets from template steps (if any)
    extra_secrets = RegistryActionsService.aggregate_secrets_from_manifest(
        result.manifest, action_name
    )
    # Remove direct secrets (already in manifest_action) to avoid duplicates
    # The from_index_and_manifest method will merge them properly
    if manifest_action.secrets:
        direct_secret_keys = {
            s.name if isinstance(s, RegistrySecret) else s.provider_id
            for s in manifest_action.secrets
        }
        extra_secrets = [
            s
            for s in extra_secrets
            if (s.name if isinstance(s, RegistrySecret) else s.provider_id)
            not in direct_secret_keys
        ]

    return RegistryActionRead.from_index_and_manifest(
        result.index_entry,
        manifest_action,
        result.origin,
        result.repository_id,
        extra_secrets=extra_secrets,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("org:settings:manage")
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
@require_scope("org:settings:manage")
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
        action = await service.get_action(action_name)
        await service.update_action(action, params)
    except RegistryError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.delete("/{action_name}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:settings:manage")
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
        action = await service.get_action(action_name)
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
