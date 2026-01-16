"""Platform-level registry sync endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.credentials import SuperuserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat_ee.admin.registry.schemas import (
    RegistryStatusResponse,
    RegistrySyncResponse,
    RegistryVersionRead,
)
from tracecat_ee.admin.registry.service import AdminRegistryService

router = APIRouter(prefix="/registry", tags=["admin:registry"])


@router.post("/sync", response_model=RegistrySyncResponse)
async def sync_all_repositories(
    role: SuperuserRole,
    session: AsyncDBSession,
) -> RegistrySyncResponse:
    """Trigger sync for all platform registry repositories."""
    service = AdminRegistryService(session, role=role)
    return await service.sync_all_repositories()


@router.post("/sync/{repository_id}", response_model=RegistrySyncResponse)
async def sync_repository(
    role: SuperuserRole,
    session: AsyncDBSession,
    repository_id: uuid.UUID,
) -> RegistrySyncResponse:
    """Trigger sync for a specific platform registry repository."""
    service = AdminRegistryService(session, role=role)
    try:
        return await service.sync_repository(repository_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/status", response_model=RegistryStatusResponse)
async def get_registry_status(
    role: SuperuserRole,
    session: AsyncDBSession,
) -> RegistryStatusResponse:
    """Get registry sync status and health."""
    service = AdminRegistryService(session, role=role)
    return await service.get_status()


@router.get("/versions", response_model=list[RegistryVersionRead])
async def list_registry_versions(
    role: SuperuserRole,
    session: AsyncDBSession,
    repository_id: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
) -> list[RegistryVersionRead]:
    """List registry versions with optional filtering."""
    service = AdminRegistryService(session, role=role)
    return list(await service.list_versions(repository_id=repository_id, limit=limit))
