from typing import Annotated

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.credentials import RoleACL
from tracecat.auth.dependencies import Role
from tracecat.auth.enums import AuthType
from tracecat.config import SAML_PUBLIC_ACS_URL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.settings.constants import AUTH_TYPE_TO_SETTING_KEY
from tracecat.settings.models import (
    AppSettingsRead,
    AppSettingsUpdate,
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
from tracecat.types.auth import AccessLevel

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
    role: OrgAdminUserRole,
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
