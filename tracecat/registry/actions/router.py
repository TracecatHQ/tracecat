from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError
from tracecat_registry import RegistrySecret

from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import RegistryError
from tracecat.registry.actions.schemas import (
    RegistryActionCreate,
    RegistryActionRead,
    RegistryActionReadMinimal,
    RegistryActionUpdate,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import REGISTRY_ACTIONS_PATH

router = APIRouter(prefix=REGISTRY_ACTIONS_PATH, tags=["registry-actions"])


@router.get("")
@require_scope("org:registry:read")
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
@require_scope("org:registry:read")
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
@require_scope("org:registry:create")
async def create_registry_action(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
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
@require_scope("org:registry:update")
async def update_registry_action(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
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
@require_scope("org:registry:delete")
async def delete_registry_action(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
    session: AsyncDBSession,
    action_name: str,
) -> None:
    """Registry actions are versioned snapshots and cannot be deleted individually."""
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Action '{action_name}' cannot be deleted individually. "
            "Registry actions are served from immutable versions. "
            "Sync or promote a new registry version to remove an action."
        ),
    )
