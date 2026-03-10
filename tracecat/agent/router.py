import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.agent.schemas import (
    AgentModelSourceCreate,
    AgentModelSourceRead,
    AgentModelSourceUpdate,
    BuiltInCatalogRead,
    BuiltInProviderRead,
    DefaultModelInventoryRead,
    DefaultModelSelection,
    DefaultModelSelectionUpdate,
    EnabledModelOperation,
    EnabledModelRuntimeConfigUpdate,
    EnabledModelsBatchOperation,
    ModelCatalogEntry,
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ProviderCredentialConfig,
)
from tracecat.agent.service import AgentManagementService
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError

router = APIRouter(prefix="/agent", tags=["agent"])

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


@router.get("/catalog/builtins")
@require_scope("agent:read")
async def list_builtin_catalog(
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
    """List the built-in model catalog with org readiness state."""
    service = AgentManagementService(session, role=role)
    return await service.list_builtin_catalog(
        query=query,
        provider=provider,
        cursor=cursor,
        limit=limit,
    )


@router.post("/catalog/builtins/refresh")
@require_scope("agent:update")
async def refresh_builtin_catalog(
    *,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> BuiltInCatalogRead:
    """Refresh the built-in model catalog state."""
    service = AgentManagementService(session, role=role)
    return await service.refresh_builtin_catalog()


@router.get("/catalog/discovered")
@require_scope("agent:read")
async def list_discovered_models(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
) -> list[ModelCatalogEntry]:
    """List discovered models with org enablement state."""
    service = AgentManagementService(session, role=role)
    return await service.list_discovered_models()


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


@router.get("/default-models")
@require_scope("agent:read")
async def get_default_model_inventory(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
) -> DefaultModelInventoryRead:
    """Get default-sidecar model inventory and sync state."""
    service = AgentManagementService(session, role=role)
    return await service.get_default_sidecar_inventory()


@router.post("/default-models/refresh")
@require_scope("agent:update")
async def refresh_default_model_inventory(
    *,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> DefaultModelInventoryRead:
    """Refresh the default-sidecar model inventory."""
    service = AgentManagementService(session, role=role)
    return await service.refresh_default_sidecar_inventory()


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


@router.post("/providers/{provider}/refresh")
@require_scope("agent:update")
async def refresh_provider_inventory(
    *,
    provider: str,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> BuiltInProviderRead:
    """Refresh a built-in provider inventory."""
    service = AgentManagementService(session, role=role)
    try:
        return await service.refresh_provider_inventory(provider)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create credentials: {str(e)}",
        ) from e


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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update credentials: {str(e)}",
        ) from e


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
        return await service.set_default_model_ref(params.catalog_ref)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to set default model: {str(e)}",
        ) from e


@router.get("/custom-sources")
@router.get("/model-sources", deprecated=True)
@require_scope("agent:read")
async def list_custom_sources(
    *,
    role: OrganizationUserRole,
    session: AsyncDBSession,
) -> list[AgentModelSourceRead]:
    service = AgentManagementService(session, role=role)
    return await service.list_model_sources()


@router.post("/custom-sources", status_code=status.HTTP_201_CREATED)
@router.post("/model-sources", status_code=status.HTTP_201_CREATED, deprecated=True)
@require_scope("agent:update")
async def create_custom_source(
    *,
    params: AgentModelSourceCreate,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> AgentModelSourceRead:
    service = AgentManagementService(session, role=role)
    try:
        return await service.create_model_source(params)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.patch("/custom-sources/{source_id}")
@router.patch("/model-sources/{source_id}", deprecated=True)
@require_scope("agent:update")
async def update_custom_source(
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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.delete("/custom-sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
@router.delete(
    "/model-sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    deprecated=True,
)
@require_scope("agent:update")
async def delete_custom_source(
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


@router.post("/custom-sources/{source_id}/refresh")
@router.post("/model-sources/{source_id}/refresh", deprecated=True)
@require_scope("agent:update")
async def refresh_custom_source(
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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


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
    catalog_ref: str,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> None:
    service = AgentManagementService(session, role=role)
    await service.disable_model(catalog_ref)


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
