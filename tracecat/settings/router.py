from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.dependencies import OrgAdminUser, OrgUserRole
from tracecat.auth.enums import AuthType
from tracecat.authz.controls import require_scope
from tracecat.config import SAML_PUBLIC_ACS_URL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import OrganizationDomain
from tracecat.identifiers import OrganizationID
from tracecat.settings.schemas import (
    AgentSettingsRead,
    AgentSettingsUpdate,
    AppSettingsRead,
    AppSettingsUpdate,
    AuditSettingsRead,
    AuditSettingsUpdate,
    GitSettingsRead,
    GitSettingsUpdate,
    SAMLSettingsRead,
    SAMLSettingsUpdate,
)
from tracecat.settings.service import SettingsService
from tracecat.tiers.entitlements import Entitlement, check_entitlement

router = APIRouter(prefix="/settings", tags=["settings"])

# NOTE: We expose settings groups
# We don't need create or delete endpoints as we only need to read/update settings.
# For M2M, we use the service directly.


async def check_other_auth_enabled(
    _service: SettingsService, auth_type: AuthType
) -> None:
    """Check if at least one other auth type is enabled."""
    if auth_type is not AuthType.SAML:
        return
    if any(
        candidate_auth_type in config.TRACECAT__AUTH_TYPES
        for candidate_auth_type in (
            AuthType.BASIC,
            AuthType.OIDC,
            AuthType.GOOGLE_OAUTH,
        )
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="At least one other auth type must be enabled",
    )


async def _organization_has_active_domain(
    *, session: AsyncSession, organization_id: OrganizationID
) -> bool:
    stmt = (
        select(OrganizationDomain.id)
        .where(
            OrganizationDomain.organization_id == organization_id,
            OrganizationDomain.is_active.is_(True),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def check_saml_domain_prerequisites(
    *, session: AsyncSession, role: OrgAdminUser, params: SAMLSettingsUpdate
) -> None:
    """Enforce domain guardrails for multi-tenant SAML enablement.

    In multi-tenant mode, enabling SAML and enabling SAML auto-provisioning must
    require at least one active organization domain. This prevents org
    enrollment on arbitrary email domains when SAML assertions are accepted.
    """
    if not config.TRACECAT__EE_MULTI_TENANT:
        return

    fields_set = params.model_fields_set
    enabling_saml = "saml_enabled" in fields_set and params.saml_enabled
    enabling_auto_provisioning = (
        "saml_auto_provisioning" in fields_set and params.saml_auto_provisioning
    )
    if not (enabling_saml or enabling_auto_provisioning):
        return

    organization_id = role.organization_id
    if organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization context",
        )

    if await _organization_has_active_domain(
        session=session, organization_id=organization_id
    ):
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "At least one active organization domain is required before enabling "
            "SAML or SAML auto-provisioning in multi-tenant mode"
        ),
    )


@router.get("/git", response_model=GitSettingsRead)
@require_scope("org:settings:read")
async def get_git_settings(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
) -> GitSettingsRead:
    await check_entitlement(session, role, Entitlement.CUSTOM_REGISTRY)
    service = SettingsService(session, role)
    keys = GitSettingsRead.keys()
    settings = await service.list_org_settings(keys=keys)
    settings_dict, _ = service.get_values_with_decryption_fallback(settings)
    return GitSettingsRead(**settings_dict)


@router.patch("/git", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:settings:update")
async def update_git_settings(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
    params: GitSettingsUpdate,
) -> None:
    await check_entitlement(session, role, Entitlement.CUSTOM_REGISTRY)
    service = SettingsService(session, role)
    await service.update_git_settings(params)


@router.get("/saml", response_model=SAMLSettingsRead)
@require_scope("org:settings:read")
async def get_saml_settings(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
) -> SAMLSettingsRead:
    service = SettingsService(session, role)

    # Exclude read-only keys
    keys = SAMLSettingsRead.keys(exclude={"saml_sp_acs_url", "decryption_failed_keys"})
    settings = await service.list_org_settings(keys=keys)
    settings_dict, decryption_failed_keys = service.get_values_with_decryption_fallback(
        settings
    )

    # Public ACS url
    return SAMLSettingsRead(
        **settings_dict,
        saml_sp_acs_url=SAML_PUBLIC_ACS_URL,
        decryption_failed_keys=decryption_failed_keys,
    )


@router.patch("/saml", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:settings:update")
async def update_saml_settings(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
    params: SAMLSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    await check_saml_domain_prerequisites(session=session, role=role, params=params)
    if not params.saml_enabled:
        await check_other_auth_enabled(service, AuthType.SAML)
    await service.update_saml_settings(params)


@router.get("/app", response_model=AppSettingsRead)
@require_scope("org:settings:read")
async def get_app_settings(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> AppSettingsRead:
    service = SettingsService(session, role)
    keys = AppSettingsRead.keys()
    settings = await service.list_org_settings(keys=keys)
    settings_dict, _ = service.get_values_with_decryption_fallback(settings)
    return AppSettingsRead(**settings_dict)


@router.patch("/app", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:settings:update")
async def update_app_settings(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
    params: AppSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    await service.update_app_settings(params)


@router.get("/audit", response_model=AuditSettingsRead)
@require_scope("org:settings:read")
async def get_audit_settings(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
) -> AuditSettingsRead:
    service = SettingsService(session, role)
    keys = AuditSettingsRead.keys(exclude={"decryption_failed_keys"})
    settings = await service.list_org_settings(keys=keys)
    settings_dict, decryption_failed_keys = service.get_values_with_decryption_fallback(
        settings
    )
    return AuditSettingsRead(
        **settings_dict, decryption_failed_keys=decryption_failed_keys
    )


@router.patch("/audit", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:settings:update")
async def update_audit_settings(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
    params: AuditSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    await service.update_audit_settings(params)


@router.get("/agent", response_model=AgentSettingsRead)
@require_scope("org:settings:read")
async def get_agent_settings(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
) -> AgentSettingsRead:
    service = SettingsService(session, role)
    keys = AgentSettingsRead.keys()
    settings = await service.list_org_settings(keys=keys)
    settings_dict, _ = service.get_values_with_decryption_fallback(settings)
    return AgentSettingsRead(**settings_dict)


@router.patch("/agent", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:settings:update")
async def update_agent_settings(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
    params: AgentSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    await service.update_agent_settings(params)
