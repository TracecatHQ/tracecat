"""HTTP wiring for agent provider credential routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from tracecat.agent.credentials.service import AgentCredentialsService
from tracecat.agent.schemas import (
    ModelCredentialCreate,
    ModelCredentialUpdate,
    ProviderCredentialConfig,
)
from tracecat.auth.dependencies import OrgUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger

router = APIRouter(tags=["agent"])


@router.get("/providers/status")
@require_scope("agent:read")
async def get_providers_status(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> dict[str, bool]:
    service = AgentCredentialsService(session, role=role)
    return await service.get_providers_status()


@router.get("/providers/configs")
@require_scope("agent:read")
async def list_provider_credential_configs(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> list[ProviderCredentialConfig]:
    service = AgentCredentialsService(session, role=role)
    return await service.list_provider_credential_configs()


@router.get("/providers/{provider}/config")
@require_scope("agent:read")
async def get_provider_credential_config(
    *,
    provider: str,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> ProviderCredentialConfig:
    service = AgentCredentialsService(session, role=role)
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
    role: OrgUserRole,
    session: AsyncDBSession,
) -> dict[str, str]:
    service = AgentCredentialsService(session, role=role)
    try:
        await service.create_provider_credentials(params)
        return {"message": f"Credentials for {params.provider} created successfully"}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.exception(
            "Unexpected agent credentials API error", action="create credentials"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create credentials.",
        ) from e


@router.put("/credentials/{provider}")
@require_scope("agent:update")
async def update_provider_credentials(
    *,
    provider: str,
    params: ModelCredentialUpdate,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> dict[str, str]:
    service = AgentCredentialsService(session, role=role)
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
        logger.exception(
            "Unexpected agent credentials API error", action="update credentials"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update credentials.",
        ) from e


@router.delete("/credentials/{provider}")
@require_scope("agent:update")
async def delete_provider_credentials(
    *,
    provider: str,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> dict[str, str]:
    service = AgentCredentialsService(session, role=role)
    await service.delete_provider_credentials(provider)
    return {"message": f"Credentials for {provider} deleted successfully"}
