from typing import Annotated

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.ee.agent.models import (
    ModelConfig,
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ProviderCredentialConfig,
)
from tracecat.ee.agent.service import AgentManagementService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatNotFoundError

router = APIRouter(prefix="/agent", tags=["agent"])

OrganizationAdminUserRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
]


@router.get("/models")
async def list_models(
    *,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> dict[str, ModelConfig]:
    """List all available AI models."""
    service = AgentManagementService(session, role=role)
    return await service.list_models()


@router.get("/providers")
async def list_providers(
    *,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> list[str]:
    """List all available AI model providers."""
    service = AgentManagementService(session, role=role)
    return await service.list_providers()


@router.get("/providers/status")
async def get_providers_status(
    *,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> dict[str, bool]:
    """Get credential status for all providers."""
    service = AgentManagementService(session, role=role)
    return await service.get_providers_status()


@router.get("/providers/configs")
async def list_provider_credential_configs(
    *,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> list[ProviderCredentialConfig]:
    """List credential field configurations for all providers."""
    service = AgentManagementService(session, role=role)
    return await service.list_provider_credential_configs()


@router.get("/providers/{provider}/config")
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
async def get_default_model(
    *,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> str | None:
    """Get the organization's default AI model."""
    service = AgentManagementService(session, role=role)
    return await service.get_default_model()


@router.put("/default-model")
async def set_default_model(
    *,
    model_name: str,
    role: OrganizationAdminUserRole,
    session: AsyncDBSession,
) -> dict[str, str]:
    """Set the organization's default AI model."""
    service = AgentManagementService(session, role=role)
    try:
        await service.set_default_model(model_name)
        return {"message": f"Default model set to {model_name}"}
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {model_name} not found",
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to set default model: {str(e)}",
        ) from e
