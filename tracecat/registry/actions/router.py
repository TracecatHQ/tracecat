from fastapi import APIRouter, HTTPException, Query, status
from tracecat_registry import RegistrySecret

from tracecat.agent.authoring_context import filter_configured_actions
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.registry.actions.schemas import (
    RegistryActionRead,
    RegistryActionReadMinimal,
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
        require_workspace="optional",
    ),
    session: AsyncDBSession,
    include_locked: bool = Query(
        default=False,
        description="Include actions locked by missing entitlements",
    ),
    configured_only: bool = Query(
        default=False,
        description="Only actions with configured credentials in the selected workspace",
    ),
) -> list[RegistryActionReadMinimal]:
    """List all actions from registry index."""
    service = RegistryActionsService(session, role)
    index_entries = await service.list_actions_from_index(include_locked=include_locked)
    if configured_only:
        if role.workspace_id is None:
            raise HTTPException(status_code=400, detail="workspace_id is required")
        configured = set(
            await filter_configured_actions(
                [f"{entry.namespace}.{entry.name}" for entry, _ in index_entries],
                registry=service,
                role=role,
            )
        )
        index_entries = [
            (entry, origin)
            for entry, origin in index_entries
            if f"{entry.namespace}.{entry.name}" in configured
        ]
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
