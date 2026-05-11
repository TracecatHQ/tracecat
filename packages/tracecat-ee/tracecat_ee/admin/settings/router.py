"""Platform settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from tracecat.auth.credentials import SuperuserRole
from tracecat.db.dependencies import AsyncDBSessionBypass
from tracecat_ee.admin.settings.schemas import (
    PlatformAuditSettingsRead,
    PlatformAuditSettingsUpdate,
    PlatformRegistrySettingsRead,
    PlatformRegistrySettingsUpdate,
)
from tracecat_ee.admin.settings.service import AdminSettingsService

router = APIRouter(prefix="/settings", tags=["admin:settings"])


@router.get("/audit", response_model=PlatformAuditSettingsRead)
async def get_audit_settings(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
) -> PlatformAuditSettingsRead:
    """Get platform audit settings."""
    service = AdminSettingsService(session, role)
    return await service.get_audit_settings()


@router.patch("/audit", response_model=PlatformAuditSettingsRead)
async def update_audit_settings(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    params: PlatformAuditSettingsUpdate,
) -> PlatformAuditSettingsRead:
    """Update platform audit settings."""
    service = AdminSettingsService(session, role)
    return await service.update_audit_settings(params)


@router.get("/registry", response_model=PlatformRegistrySettingsRead)
async def get_registry_settings(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
) -> PlatformRegistrySettingsRead:
    """Get platform registry settings."""
    service = AdminSettingsService(session, role)
    return await service.get_registry_settings()


@router.patch("/registry", response_model=PlatformRegistrySettingsRead)
async def update_registry_settings(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    params: PlatformRegistrySettingsUpdate,
) -> PlatformRegistrySettingsRead:
    """Update platform registry settings."""
    service = AdminSettingsService(session, role)
    return await service.update_registry_settings(params)
