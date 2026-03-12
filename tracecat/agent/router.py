import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.agent.schemas import (
    AgentModelSourceCreate,
    AgentModelSourceRead,
    AgentModelSourceUpdate,
    BuiltInCatalogRead,
    BuiltInProviderRead,
    DefaultModelSelection,
    DefaultModelSelectionUpdate,
    EnabledModelOperation,
    EnabledModelRuntimeConfigUpdate,
    EnabledModelsBatchOperation,
    ModelCatalogEntry,
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ProviderCredentialConfig,
    WorkspaceModelSubsetRead,
    WorkspaceModelSubsetUpdate,
)
from tracecat.agent.service import AgentManagementService
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope, require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger

router = APIRouter(prefix="/agent", tags=["agent"])


def _raise_unexpected_agent_api_error(*, action: str, exc: Exception) -> NoReturn:
    logger.exception("Unexpected agent API error", action=action)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Failed to {action}.",
    ) from exc


OrganizationAdminUserRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
]

OrganizationUserRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
]

WorkspaceUserInPath = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        workspace_id_in_path=True,
    ),
]


def _require_org_workspace_access(role: Role) -> None:
    scopes = role.scopes if role.scopes is not None else frozenset[str]()
    if has_scope(scopes, "org:workspace:read"):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.get("/models")
@require_scope("agent:read")
async def list_models(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
    workspace_id: uuid.UUID | None = Query(
        default=None,
        description="Optional workspace filter for workspace-level enabled model subsets.",
    ),
) -> list[ModelCatalogEntry]:
    """List all available AI models."""
    service = AgentManagementService(session, role=role)
    return await service.list_models(workspace_id=workspace_id)


@router.get("/workspaces/{workspace_id}/model-subset")
@require_scope("workspace:read")
async def get_workspace_model_subset(
    *,
    role: OrganizationUserRole,
    workspace_id: uuid.UUID,
    session: AsyncDBSession,
) -> WorkspaceModelSubsetRead:
    _require_org_workspace_access(role)
    service = AgentManagementService(session, role=role)
    return await service.get_workspace_model_subset(workspace_id)


@router.put("/workspaces/{workspace_id}/model-subset")
@require_scope("workspace:update")
async def replace_workspace_model_subset(
    *,
    role: OrganizationUserRole,
    workspace_id: uuid.UUID,
    params: WorkspaceModelSubsetUpdate,
    session: AsyncDBSession,
) -> WorkspaceModelSubsetRead:
    _require_org_workspace_access(role)
    service = AgentManagementService(session, role=role)
    try:
        return await service.replace_workspace_model_subset(workspace_id, params)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.delete(
    "/workspaces/{workspace_id}/model-subset", status_code=status.HTTP_204_NO_CONTENT
)
@require_scope("workspace:update")
async def clear_workspace_model_subset(
    *,
    role: OrganizationUserRole,
    workspace_id: uuid.UUID,
    session: AsyncDBSession,
) -> None:
    _require_org_workspace_access(role)
    service = AgentManagementService(session, role=role)
    try:
        await service.clear_workspace_model_subset(workspace_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/catalog/platform")
@require_scope("agent:read")
async def list_platform_catalog(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
    query: str | None = Query(default=None, description="Search models by name."),
    provider: str | None = Query(default=None, description="Filter by provider."),
    cursor: str | None = Query(
        default=None,
        description="Opaque cursor for the next built-in catalog page.",
    ),
    limit: int = Query(default=100, ge=1, le=200),
) -> BuiltInCatalogRead:
    """List the shared platform catalog with org readiness state."""
    service = AgentManagementService(session, role=role)
    return await service.list_builtin_catalog(
        query=query,
        provider=provider,
        cursor=cursor,
        limit=limit,
    )


@router.get("/providers")
@require_scope("agent:read")
async def list_providers(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
) -> list[BuiltInProviderRead]:
    """List built-in providers with discovery and credential state."""
    service = AgentManagementService(session, role=role)
    return await service.list_providers()


@router.get("/providers/status")
@require_scope("agent:read")
async def get_providers_status(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
) -> dict[str, bool]:
    """Get credential status for all providers."""
    service = AgentManagementService(session, role=role)
    return await service.get_providers_status()


@router.get("/providers/configs")
@require_scope("agent:read")
async def list_provider_credential_configs(
    *,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> list[ProviderCredentialConfig]:
    """List credential field configurations for all providers."""
    service = AgentManagementService(session, role=role)
    return await service.list_provider_credential_configs()


@router.get("/providers/{provider}/config")
@require_scope("agent:read")
async def get_provider_credential_config(
    *,
    provider: str,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> ProviderCredentialConfig:
    """Get credential field configuration for a specific provider."""
    service = AgentManagementService(session, role=role)
    try:
        return await service.get_provider_credential_config(provider)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider {provider} not found",
        ) from e


@router.post("/credentials", status_code=status.HTTP_201_CREATED)
@require_scope("agent:update")
async def create_provider_credentials(
    *,
    params: ModelCredentialCreate,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> dict[str, str]:
    """Create or update credentials for an AI provider."""
    service = AgentManagementService(session, role=role)
    try:
        await service.create_provider_credentials(params)
        return {"message": f"Credentials for {params.provider} created successfully"}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        _raise_unexpected_agent_api_error(action="create credentials", exc=e)


@router.put("/credentials/{provider}")
@require_scope("agent:update")
async def update_provider_credentials(
    *,
    provider: str,
    params: ModelCredentialUpdate,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> dict[str, str]:
    """Update existing credentials for an AI provider."""
    service = AgentManagementService(session, role=role)
    try:
        await service.update_provider_credentials(provider, params)
        return {"message": f"Credentials for {provider} updated successfully"}
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credentials for provider {provider} not found",
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        _raise_unexpected_agent_api_error(action="update credentials", exc=e)


@router.delete("/credentials/{provider}")
@require_scope("agent:update")
async def delete_provider_credentials(
    *,
    provider: str,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> dict[str, str]:
    """Delete credentials for an AI provider."""
    service = AgentManagementService(session, role=role)
    await service.delete_provider_credentials(provider)
    return {"message": f"Credentials for {provider} deleted successfully"}


@router.get("/default-model")
@require_scope("agent:read")
async def get_default_model(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
) -> DefaultModelSelection | None:
    """Get the organization's default AI model."""
    service = AgentManagementService(session, role=role)
    return await service.get_default_model()


@router.put("/default-model")
@require_scope("agent:update")
async def set_default_model(
    *,
    params: DefaultModelSelectionUpdate,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> DefaultModelSelection:
    """Set the organization's default AI model."""
    service = AgentManagementService(session, role=role)
    try:
        return await service.set_default_model_selection(params)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        _raise_unexpected_agent_api_error(action="set default model", exc=e)


@router.get("/sources")
@require_scope("agent:read")
async def list_sources(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
) -> list[AgentModelSourceRead]:
    service = AgentManagementService(session, role=role)
    return await service.list_model_sources()


@router.post("/sources", status_code=status.HTTP_201_CREATED)
@require_scope("agent:update")
async def create_source(
    *,
    params: AgentModelSourceCreate,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> AgentModelSourceRead:
    service = AgentManagementService(session, role=role)
    try:
        return await service.create_model_source(params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        _raise_unexpected_agent_api_error(action="create source", exc=e)


@router.patch("/sources/{source_id}")
@require_scope("agent:update")
async def update_source(
    *,
    source_id: uuid.UUID,
    params: AgentModelSourceUpdate,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> AgentModelSourceRead:
    service = AgentManagementService(session, role=role)
    try:
        return await service.update_model_source(source_id=source_id, params=params)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except Exception as e:
        _raise_unexpected_agent_api_error(action="update source", exc=e)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def delete_source(
    *,
    source_id: uuid.UUID,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> None:
    service = AgentManagementService(session, role=role)
    try:
        await service.delete_model_source(source_id=source_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/sources/{source_id}/refresh")
@require_scope("agent:update")
async def refresh_source(
    *,
    source_id: uuid.UUID,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> list[ModelCatalogEntry]:
    service = AgentManagementService(session, role=role)
    try:
        return await service.refresh_model_source(source_id=source_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except Exception as e:
        _raise_unexpected_agent_api_error(action="refresh source", exc=e)


@router.post("/models/enabled")
@require_scope("agent:update")
async def enable_model(
    *,
    params: EnabledModelOperation,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> ModelCatalogEntry:
    service = AgentManagementService(session, role=role)
    try:
        return await service.enable_model(params)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/models/enabled/batch")
@require_scope("agent:update")
async def enable_models(
    *,
    params: EnabledModelsBatchOperation,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> list[ModelCatalogEntry]:
    service = AgentManagementService(session, role=role)
    try:
        return await service.enable_models(params)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.delete("/models/enabled", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def disable_model(
    *,
    source_id: uuid.UUID | None = Query(default=None),
    model_provider: str = Query(...),
    model_name: str = Query(...),
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> None:
    service = AgentManagementService(session, role=role)
    await service.disable_model(
        EnabledModelOperation(
            source_id=source_id,
            model_provider=model_provider,
            model_name=model_name,
        )
    )


@router.delete("/models/enabled/batch", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def disable_models(
    *,
    params: EnabledModelsBatchOperation,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> None:
    service = AgentManagementService(session, role=role)
    await service.disable_models(params)


@router.patch("/models/enabled")
@require_scope("agent:update")
async def update_enabled_model_config(
    *,
    params: EnabledModelRuntimeConfigUpdate,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> ModelCatalogEntry:
    service = AgentManagementService(session, role=role)
    try:
        return await service.update_enabled_model_config(params)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
