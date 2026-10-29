from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.audit.service import (
    AuditService,
    AuditWebhookNotConfiguredError,
    AuditWebhookUrlNotAllowedError,
)
from tracecat.auth.dependencies import OrgActorRole, OrgUserRole
from tracecat.auth.enums import AuthType
from tracecat.auth.ip_allowlist_enforcement import (
    check_ip_against_allowlists,
    clear_ip_allowlist_cache,
    ensure_allowlist_admits_caller,
)
from tracecat.auth.mfa.constants import ORG_MFA_REQUIRED_KEY
from tracecat.auth.mfa.policy import (
    clear_mfa_policy_cache,
    ensure_can_enable_mfa_policy,
)
from tracecat.auth.mfa.schemas import MfaPolicyRead, MfaPolicyUpdate
from tracecat.auth.mfa.webauthn import webauthn_unavailable_reason
from tracecat.authz.controls import require_scope
from tracecat.config import SAML_PUBLIC_ACS_URL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import OrganizationDomain
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers import OrganizationID
from tracecat.settings.schemas import (
    AgentOtelSettingsRead,
    AgentOtelSettingsUpdate,
    AgentSettingsRead,
    AgentSettingsUpdate,
    AppSettingsRead,
    AppSettingsUpdate,
    AuditSettingsRead,
    AuditSettingsUpdate,
    AuditWebhookTestResult,
    GitSettingsRead,
    GitSettingsUpdate,
    IPAllowlistCheckRequest,
    IPAllowlistCheckResult,
    SAMLSettingsRead,
    SAMLSettingsUpdate,
    SecuritySettingsRead,
    SecuritySettingsUpdate,
    SettingCreate,
    SettingUpdate,
    parse_stored_ip_allowlists,
)
from tracecat.settings.service import (
    AgentOtelEndpointNotAllowedError,
    SettingsService,
)
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement

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
    *, session: AsyncSession, role: OrgUserRole, params: SAMLSettingsUpdate
) -> None:
    """Enforce domain guardrails for multi-tenant SAML enablement.

    In multi-tenant mode, enabling SAML must require at least one active
    organization domain. This prevents org enrollment on arbitrary email domains
    when SAML assertions are accepted.
    """
    if not config.TRACECAT__EE_MULTI_TENANT:
        return

    fields_set = params.model_fields_set
    enabling_saml = "saml_enabled" in fields_set and params.saml_enabled
    if not enabling_saml:
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
            "SAML in multi-tenant mode"
        ),
    )


@router.get("/git", response_model=GitSettingsRead)
@require_scope("org:settings:read")
async def get_git_settings(
    *,
    role: OrgActorRole,
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
    role: OrgActorRole,
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
    role: OrgUserRole,
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
    role: OrgUserRole,
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
    role: OrgUserRole,
    session: AsyncDBSession,
    params: AppSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    await service.update_app_settings(params)


@router.get("/audit", response_model=AuditSettingsRead)
@require_scope("org:settings:read")
async def get_audit_settings(
    *,
    role: OrgUserRole,
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
    role: OrgUserRole,
    session: AsyncDBSession,
    params: AuditSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    await service.update_audit_settings(params)


@router.get("/security", response_model=SecuritySettingsRead)
@require_scope("org:settings:read")
async def get_security_settings(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> SecuritySettingsRead:
    service = SettingsService(session, role)
    return await _load_security_settings(service)


async def _load_security_settings(service: SettingsService) -> SecuritySettingsRead:
    settings = await service.list_org_settings(keys=SecuritySettingsRead.keys())
    settings_dict = {s.key: service.get_value(s) for s in settings}
    return SecuritySettingsRead(
        ip_allowlist_enabled=bool(settings_dict.get("ip_allowlist_enabled", False)),
        ip_allowlists=parse_stored_ip_allowlists(settings_dict.get("ip_allowlists")),
    )


@router.get("/security/mfa", response_model=MfaPolicyRead)
@require_scope("org:settings:read")
async def get_mfa_policy(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> MfaPolicyRead:
    """Whether members must sign in with a security key."""
    service = SettingsService(session, role)
    setting = await service.get_org_setting(ORG_MFA_REQUIRED_KEY)
    reason = webauthn_unavailable_reason()
    return MfaPolicyRead(
        mfa_required=setting is not None and service.get_value(setting) is True,
        webauthn_available=reason is None,
        webauthn_unavailable_reason=reason,
    )


@router.patch("/security/mfa", response_model=MfaPolicyRead)
@require_scope("org:settings:update")
async def update_mfa_policy(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: MfaPolicyUpdate,
) -> MfaPolicyRead:
    """Require (or stop requiring) a security key at sign-in for all members.

    Applies to password, OIDC, and SAML sign-in. Members without a key must
    register one before they can continue. The caller must already have one.
    """
    if params.mfa_required:
        if role.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A user is required to enable this setting",
            )
        try:
            await ensure_can_enable_mfa_policy(session, role.user_id)
        except TracecatValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
            ) from e
    service = SettingsService(session, role)
    if setting := await service.get_org_setting(ORG_MFA_REQUIRED_KEY):
        await service.update_org_setting(
            setting, SettingUpdate(value=params.mfa_required)
        )
    else:
        await service.create_org_setting(
            SettingCreate(
                key=ORG_MFA_REQUIRED_KEY,
                value=params.mfa_required,
                is_sensitive=False,
            )
        )
    clear_mfa_policy_cache()
    return await get_mfa_policy(role=role, session=session)


@router.patch("/security", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:settings:update")
async def update_security_settings(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: SecuritySettingsUpdate,
) -> None:
    """Update the organization IP allowlist.

    Enabling a non-empty allowlist that excludes the caller's own IP is
    rejected so an admin cannot lock themselves out of the organization.
    """
    ensure_allowlist_admits_caller(
        enabled=params.ip_allowlist_enabled, cidrs=params.cidrs
    )
    service = SettingsService(session, role)
    await service.update_security_settings(params)
    clear_ip_allowlist_cache()


@router.post("/security/ip-allowlist/check", response_model=IPAllowlistCheckResult)
@require_scope("org:settings:read")
async def check_ip_allowlist(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: IPAllowlistCheckRequest,
) -> IPAllowlistCheckResult:
    """Report whether an IP address is admitted by the saved allowlist."""
    service = SettingsService(session, role)
    saved = await _load_security_settings(service)
    return check_ip_against_allowlists(
        params.ip_address,
        enabled=saved.ip_allowlist_enabled,
        allowlists=saved.ip_allowlists,
    )


@router.post("/audit/test", response_model=AuditWebhookTestResult)
@require_scope("org:settings:update")
async def test_audit_webhook(
    *,
    role: OrgUserRole,
    params: AuditSettingsUpdate,
) -> AuditWebhookTestResult:
    """Probe the submitted audit webhook configuration with a marked test event."""
    try:
        return await AuditService.probe_webhook(
            sink="organization",
            organization_id=role.organization_id,
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


@router.get("/agent", response_model=AgentSettingsRead)
@require_scope("org:settings:read")
async def get_agent_settings(
    *,
    role: OrgUserRole,
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
    role: OrgUserRole,
    session: AsyncDBSession,
    params: AgentSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    await service.update_agent_settings(params)


@router.get("/agent-otel", response_model=AgentOtelSettingsRead)
@require_scope("org:settings:read")
async def get_agent_otel_settings(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> AgentOtelSettingsRead:
    service = SettingsService(session, role)
    keys = AgentOtelSettingsRead.keys()
    settings = await service.list_org_settings(keys=keys)
    settings_dict, _ = service.get_values_with_decryption_fallback(settings)
    return AgentOtelSettingsRead(**settings_dict)


@router.patch("/agent-otel", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:settings:update")
async def update_agent_otel_settings(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: AgentOtelSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    try:
        await service.update_agent_otel_settings(params)
    except AgentOtelEndpointNotAllowedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent OTel endpoint is not allowed",
        ) from exc
