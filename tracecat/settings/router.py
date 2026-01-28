from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.credentials import RoleACL
from tracecat.auth.dependencies import Role
from tracecat.auth.enums import AuthType
from tracecat.auth.types import AccessLevel
from tracecat.config import SAML_PUBLIC_ACS_URL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.settings.constants import AUTH_TYPE_TO_SETTING_KEY
from tracecat.settings.schemas import (
    AgentSettingsRead,
    AgentSettingsUpdate,
    AppSettingsRead,
    AppSettingsUpdate,
    AuditApiKeyGenerateResponse,
    AuditSettingsRead,
    AuditSettingsUpdate,
    AuthSettingsRead,
    AuthSettingsUpdate,
    GitSettingsRead,
    GitSettingsUpdate,
    OAuthSettingsRead,
    OAuthSettingsUpdate,
    SAMLSettingsRead,
    SAMLSettingsUpdate,
)
from tracecat.settings.service import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])

OrgAdminUserRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
]

OrgUserRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
    ),
]

# NOTE: We expose settings groups
# We don't need create or delete endpoints as we only need to read/update settings.
# For M2M, we use the service directly.


async def check_other_auth_enabled(
    service: SettingsService, auth_type: AuthType
) -> None:
    """Check if at least one other auth type is enabled."""

    all_keys = set(AUTH_TYPE_TO_SETTING_KEY.values())
    all_keys.remove(AUTH_TYPE_TO_SETTING_KEY[auth_type])
    for key in all_keys:
        setting = await service.get_org_setting(key)
        if setting and service.get_value(setting) is True:
            return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="At least one other auth type must be enabled",
    )


@router.get("/git", response_model=GitSettingsRead)
async def get_git_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
) -> GitSettingsRead:
    service = SettingsService(session, role)
    keys = GitSettingsRead.keys()
    settings = await service.list_org_settings(keys=keys)
    settings_dict = {setting.key: service.get_value(setting) for setting in settings}
    return GitSettingsRead(**settings_dict)


@router.patch("/git", status_code=status.HTTP_204_NO_CONTENT)
async def update_git_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
    params: GitSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    await service.update_git_settings(params)


@router.get("/saml", response_model=SAMLSettingsRead)
async def get_saml_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
) -> SAMLSettingsRead:
    service = SettingsService(session, role)

    # Exclude read-only keys
    keys = SAMLSettingsRead.keys(exclude={"saml_sp_acs_url"})
    settings = await service.list_org_settings(keys=keys)
    settings_dict = {setting.key: service.get_value(setting) for setting in settings}

    # Public ACS url
    return SAMLSettingsRead(**settings_dict, saml_sp_acs_url=SAML_PUBLIC_ACS_URL)


@router.patch("/saml", status_code=status.HTTP_204_NO_CONTENT)
async def update_saml_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
    params: SAMLSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    if not params.saml_enabled:
        await check_other_auth_enabled(service, AuthType.SAML)
    await service.update_saml_settings(params)


@router.get("/auth", response_model=AuthSettingsRead)
async def get_auth_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
) -> AuthSettingsRead:
    service = SettingsService(session, role)
    keys = AuthSettingsRead.keys()
    settings = await service.list_org_settings(keys=keys)
    settings_dict = {setting.key: service.get_value(setting) for setting in settings}
    return AuthSettingsRead(**settings_dict)


@router.patch("/auth", status_code=status.HTTP_204_NO_CONTENT)
async def update_auth_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
    params: AuthSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    if not params.auth_basic_enabled:
        await check_other_auth_enabled(service, AuthType.BASIC)
    await service.update_auth_settings(params)


@router.get("/oauth", response_model=OAuthSettingsRead)
async def get_oauth_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
) -> OAuthSettingsRead:
    service = SettingsService(session, role)
    keys = OAuthSettingsRead.keys()
    settings = await service.list_org_settings(keys=keys)
    settings_dict = {setting.key: service.get_value(setting) for setting in settings}
    return OAuthSettingsRead(**settings_dict)


@router.patch("/oauth", status_code=status.HTTP_204_NO_CONTENT)
async def update_oauth_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
    params: OAuthSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    # If we're trying to disable OAuth, we must have at least one other auth type enabled
    if not params.oauth_google_enabled:
        await check_other_auth_enabled(service, AuthType.GOOGLE_OAUTH)
    await service.update_oauth_settings(params)


@router.get("/app", response_model=AppSettingsRead)
async def get_app_settings(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> AppSettingsRead:
    service = SettingsService(session, role)
    keys = AppSettingsRead.keys()
    settings = await service.list_org_settings(keys=keys)
    settings_dict = {setting.key: service.get_value(setting) for setting in settings}
    return AppSettingsRead(**settings_dict)


@router.patch("/app", status_code=status.HTTP_204_NO_CONTENT)
async def update_app_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
    params: AppSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    await service.update_app_settings(params)


@router.get("/audit", response_model=AuditSettingsRead)
async def get_audit_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
) -> AuditSettingsRead:
    service = SettingsService(session, role)
    keys = AuditSettingsRead.keys()
    settings = await service.list_org_settings(keys=keys)
    settings_dict = {setting.key: service.get_value(setting) for setting in settings}

    # Parse created_at from ISO string to datetime
    api_key_created_at_str = settings_dict.get("audit_webhook_api_key_created_at")
    api_key_created_at: datetime | None = None
    if api_key_created_at_str:
        try:
            api_key_created_at = datetime.fromisoformat(api_key_created_at_str)
        except (ValueError, TypeError):
            pass
    settings_dict["audit_webhook_api_key_created_at"] = api_key_created_at

    return AuditSettingsRead(**settings_dict)


@router.patch("/audit", status_code=status.HTTP_204_NO_CONTENT)
async def update_audit_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
    params: AuditSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    await service.update_audit_settings(params)


@router.post("/audit/api-key", response_model=AuditApiKeyGenerateResponse)
async def generate_audit_api_key(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
) -> AuditApiKeyGenerateResponse:
    """Generate a new API key for the audit webhook.

    This replaces any existing key. The raw API key is shown only once.
    """
    service = SettingsService(session, role)
    return await service.generate_audit_api_key()


@router.delete("/audit/api-key", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_audit_api_key(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
) -> None:
    """Revoke the current audit webhook API key."""
    service = SettingsService(session, role)
    await service.revoke_audit_api_key()


@router.get("/agent", response_model=AgentSettingsRead)
async def get_agent_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
) -> AgentSettingsRead:
    service = SettingsService(session, role)
    keys = AgentSettingsRead.keys()
    settings = await service.list_org_settings(keys=keys)
    settings_dict = {setting.key: service.get_value(setting) for setting in settings}
    return AgentSettingsRead(**settings_dict)


@router.patch("/agent", status_code=status.HTTP_204_NO_CONTENT)
async def update_agent_settings(
    *,
    role: OrgAdminUserRole,
    session: AsyncDBSession,
    params: AgentSettingsUpdate,
) -> None:
    service = SettingsService(session, role)
    await service.update_agent_settings(params)
