"""Platform-level registry management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.admin.registry.schemas import (
    RegistryStatusResponse,
    RegistrySyncResponse,
    RegistryVersionPromoteResponse,
    RegistryVersionRead,
)
from tracecat.admin.registry.service import AdminRegistryService
from tracecat.auth.credentials import SuperuserRole
from tracecat.db.dependencies import AsyncDBSessionBypass
from tracecat.registry.repositories.schemas import (
    RegistryRepositoryRead,
    RegistryRepositoryReadMinimal,
)

router = APIRouter(prefix="/registry", tags=["admin:registry"])


@router.get("/repos", response_model=list[RegistryRepositoryReadMinimal])
async def list_platform_repositories(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
) -> list[RegistryRepositoryReadMinimal]:
    """List all platform registry repositories."""
    service = AdminRegistryService(session, role)
    return await service.list_repositories()


@router.get("/repos/{repository_id}", response_model=RegistryRepositoryRead)
async def get_platform_repository(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    repository_id: uuid.UUID,
) -> RegistryRepositoryRead:
    """Get a specific platform registry repository."""
    service = AdminRegistryService(session, role)
    try:
        return await service.get_repository(repository_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/sync", response_model=RegistrySyncResponse)
async def sync_all_repositories(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    force: bool = Query(False, description="Force sync by deleting existing version"),
) -> RegistrySyncResponse:
    """Trigger sync for all platform registry repositories."""
    service = AdminRegistryService(session, role)
    return await service.sync_all_repositories(force=force)


@router.post("/sync/{repository_id}", response_model=RegistrySyncResponse)
async def sync_repository(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    repository_id: uuid.UUID,
    force: bool = Query(False, description="Force sync by deleting existing version"),
) -> RegistrySyncResponse:
    """Trigger sync for a specific platform registry repository."""
    service = AdminRegistryService(session, role)
    try:
        return await service.sync_repository(repository_id, force=force)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/status", response_model=RegistryStatusResponse)
async def get_registry_status(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
) -> RegistryStatusResponse:
    """Get registry sync status and health."""
    service = AdminRegistryService(session, role)
    return await service.get_status()


@router.get("/versions", response_model=list[RegistryVersionRead])
async def list_registry_versions(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    repository_id: uuid.UUID | None = Query(None),
    limit: int = Query(
        config.TRACECAT__LIMIT_REGISTRY_VERSIONS_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
) -> list[RegistryVersionRead]:
    """List registry versions with optional filtering."""
    service = AdminRegistryService(session, role)
    return list(await service.list_versions(repository_id=repository_id, limit=limit))


@router.post(
    "/{repository_id}/versions/{version_id}/promote",
    response_model=RegistryVersionPromoteResponse,
)
async def promote_registry_version(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    repository_id: uuid.UUID,
    version_id: uuid.UUID,
) -> RegistryVersionPromoteResponse:
    """Promote a registry version to be the current version for a repository."""
    service = AdminRegistryService(session, role)
    try:
        return await service.promote_version(
            repository_id=repository_id, version_id=version_id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
