"""Platform settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from tracecat.auth.credentials import SuperuserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat_ee.admin.settings.schemas import (
    PlatformRegistrySettingsRead,
    PlatformRegistrySettingsUpdate,
)
from tracecat_ee.admin.settings.service import AdminSettingsService

router = APIRouter(prefix="/settings", tags=["admin:settings"])


@router.get("/registry", response_model=PlatformRegistrySettingsRead)
async def get_registry_settings(
    role: SuperuserRole,
    session: AsyncDBSession,
) -> PlatformRegistrySettingsRead:
    """Get platform registry settings."""
    service = AdminSettingsService(session, role)
    return await service.get_registry_settings()


@router.patch("/registry", response_model=PlatformRegistrySettingsRead)
async def update_registry_settings(
    role: SuperuserRole,
    session: AsyncDBSession,
    params: PlatformRegistrySettingsUpdate,
) -> PlatformRegistrySettingsRead:
    """Update platform registry settings."""
    service = AdminSettingsService(session, role)
    return await service.update_registry_settings(params)
