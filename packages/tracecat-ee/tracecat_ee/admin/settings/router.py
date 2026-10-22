"""Platform settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from tracecat.audit.service import (
    AuditService,
    AuditWebhookNotConfiguredError,
    AuditWebhookUrlNotAllowedError,
)
from tracecat.auth.credentials import SuperuserRole
from tracecat.auth.ip_allowlist_enforcement import (
    check_ip_against_allowlists,
    clear_ip_allowlist_cache,
    ensure_allowlist_admits_caller,
)
from tracecat.db.dependencies import AsyncDBSessionBypass
from tracecat.settings.schemas import (
    AuditWebhookTestResult,
    IPAllowlistCheckRequest,
    IPAllowlistCheckResult,
)
from tracecat_ee.admin.settings.schemas import (
    PlatformAuditSettingsRead,
    PlatformAuditSettingsUpdate,
    PlatformRegistrySettingsRead,
    PlatformRegistrySettingsUpdate,
    PlatformSecuritySettingsRead,
    PlatformSecuritySettingsUpdate,
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


@router.post("/audit/test", response_model=AuditWebhookTestResult)
async def test_audit_webhook(
    role: SuperuserRole,
    params: PlatformAuditSettingsUpdate,
) -> AuditWebhookTestResult:
    """Probe the submitted platform audit webhook configuration."""
    try:
        return await AuditService.probe_webhook(
            sink="platform",
            organization_id=None,
            role=role,
            settings=params,
        )
    except AuditWebhookNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audit webhook is not configured",
        ) from exc
    except AuditWebhookUrlNotAllowedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audit webhook URL is not allowed",
        ) from exc


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


@router.get("/security", response_model=PlatformSecuritySettingsRead)
async def get_security_settings(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
) -> PlatformSecuritySettingsRead:
    """Get the platform admin IP allowlist."""
    service = AdminSettingsService(session, role)
    return await service.get_security_settings()


@router.patch("/security", response_model=PlatformSecuritySettingsRead)
async def update_security_settings(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    params: PlatformSecuritySettingsUpdate,
) -> PlatformSecuritySettingsRead:
    """Update the platform admin IP allowlist.

    Enabling a non-empty allowlist that excludes the caller's own IP is
    rejected so a platform admin cannot lock themselves out.
    """
    ensure_allowlist_admits_caller(
        enabled=params.ip_allowlist_enabled, cidrs=params.cidrs
    )
    service = AdminSettingsService(session, role)
    result = await service.update_security_settings(params)
    clear_ip_allowlist_cache()
    return result


@router.post("/security/ip-allowlist/check", response_model=IPAllowlistCheckResult)
async def check_ip_allowlist(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    params: IPAllowlistCheckRequest,
) -> IPAllowlistCheckResult:
    """Report whether an IP address is admitted by the saved platform admin allowlist."""
    service = AdminSettingsService(session, role)
    saved = await service.get_security_settings()
    return check_ip_against_allowlists(
        params.ip_address,
        enabled=saved.ip_allowlist_enabled,
        allowlists=saved.ip_allowlists,
    )
