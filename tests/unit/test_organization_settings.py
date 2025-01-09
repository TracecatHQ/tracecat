import orjson
import pytest
from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.enums import AuthType
from tracecat.contexts import ctx_role
from tracecat.settings.models import (
    AuthSettingsUpdate,
    GitSettingsUpdate,
    OAuthSettingsUpdate,
    SAMLSettingsUpdate,
    SettingCreate,
    SettingUpdate,
    ValueType,
)
from tracecat.settings.router import check_other_auth_enabled
from tracecat.settings.service import SettingsService, get_setting
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(scope="function")
async def settings_service(
    session: AsyncSession, svc_admin_role: Role
) -> SettingsService:
    """Create a settings service instance for testing."""
    return SettingsService(session=session, role=svc_admin_role)


@pytest.fixture(scope="function")
async def settings_service_with_defaults(
    session: AsyncSession, svc_admin_role: Role
) -> SettingsService:
    """Create a settings service instance for testing."""
    service = SettingsService(session=session, role=svc_admin_role)
    await service.init_default_settings()
    return service


@pytest.fixture
def create_params() -> SettingCreate:
    """Sample setting creation parameters."""
    return SettingCreate(
        key="test-setting",
        value={"test": "value"},
        value_type=ValueType.JSON,
        is_sensitive=False,
    )


@pytest.mark.anyio
async def test_create_and_get_org_setting(
    settings_service: SettingsService, create_params: SettingCreate
) -> None:
    """Test creating and retrieving a setting."""
    # Create setting
    created_setting = await settings_service.create_org_setting(create_params)
    assert created_setting.key == create_params.key
    assert settings_service.get_value(created_setting) == create_params.value
    assert created_setting.value_type == create_params.value_type
    assert created_setting.is_encrypted == create_params.is_sensitive

    # Retrieve setting
    retrieved_setting = await settings_service.get_org_setting(created_setting.key)
    assert retrieved_setting is not None
    assert retrieved_setting.id == created_setting.id
    assert retrieved_setting.key == create_params.key
    assert settings_service.get_value(retrieved_setting) == settings_service.get_value(
        created_setting
    )


@pytest.mark.anyio
async def test_list_org_settings(
    settings_service: SettingsService, create_params: SettingCreate
) -> None:
    """Test listing settings."""
    # Create multiple settings
    setting1 = await settings_service.create_org_setting(create_params)
    setting2 = await settings_service.create_org_setting(
        SettingCreate(
            key="test-setting-2",
            value={"other": "value"},
            value_type=ValueType.JSON,
            is_sensitive=True,
        )
    )

    # List all settings
    settings = await settings_service.list_org_settings()
    assert len(settings) >= 2
    setting_keys = {setting.key for setting in settings}
    assert setting1.key in setting_keys
    assert setting2.key in setting_keys


@pytest.mark.anyio
async def test_update_setting_admin(
    settings_service: SettingsService, create_params: SettingCreate
) -> None:
    """Test updating a setting as an admin."""
    # Create initial setting
    created_setting = await settings_service.create_org_setting(create_params)

    # Update parameters
    update_params = SettingUpdate(
        value={"updated": "value"},
        value_type=ValueType.JSON,
    )

    # Update setting
    updated_setting = await settings_service.update_org_setting(
        created_setting, params=update_params
    )
    assert settings_service.get_value(updated_setting) == update_params.value
    assert updated_setting.value_type == update_params.value_type

    # Verify updates persisted
    retrieved_setting = await settings_service.get_org_setting(created_setting.key)
    assert retrieved_setting is not None
    assert settings_service.get_value(retrieved_setting) == update_params.value
    assert retrieved_setting.value_type == update_params.value_type


@pytest.mark.anyio
async def test_get_nonexistent_setting(settings_service: SettingsService) -> None:
    """Test getting a setting that doesn't exist."""
    setting = await settings_service.get_org_setting("nonexistent-key")
    assert setting is None


@pytest.mark.anyio
async def test_sensitive_setting_handling(settings_service: SettingsService) -> None:
    """Test handling of encrypted settings."""
    sensitive_setting = await settings_service.create_org_setting(
        SettingCreate(
            key="sensitive-setting",
            value=orjson.dumps({"secret": "value"}),
            value_type=ValueType.JSON,
            is_sensitive=True,
        )
    )

    assert sensitive_setting.is_encrypted is True
    retrieved = await settings_service.get_org_setting(sensitive_setting.key)
    assert retrieved is not None
    assert retrieved.is_encrypted is True


@pytest.mark.anyio
async def test_delete_system_setting(
    settings_service: SettingsService,
    create_params: SettingCreate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test attempting to delete an system setting, which should fail."""
    # Create system setting
    monkeypatch.setattr(settings_service, "_system_keys", lambda: {create_params.key})

    system_setting = await settings_service.create_org_setting(create_params)

    # Attempt to delete setting, should raise an error
    await settings_service.delete_org_setting(system_setting)

    # Verify setting still exists
    retrieved_setting = await settings_service.get_org_setting(system_setting.key)
    assert retrieved_setting is not None


@pytest.mark.anyio
async def test_delete_non_system_setting(
    settings_service: SettingsService,
    create_params: SettingCreate,
) -> None:
    """Test deleting a setting."""
    # Create setting
    created_setting = await settings_service.create_org_setting(create_params)

    # Delete setting
    await settings_service.delete_org_setting(created_setting)

    # Verify deletion
    retrieved_setting = await settings_service.get_org_setting(created_setting.key)
    assert retrieved_setting is None


@pytest.mark.anyio
async def test_update_git_settings(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Test updating Git settings."""
    # Update Git settings with allowed domains and repo info
    service = settings_service_with_defaults
    test_params = GitSettingsUpdate(
        git_allowed_domains=["github.com", "gitlab.com"],
        git_repo_url="https://github.com/test/repo",
        git_repo_package_name="test-package",
    )
    await service.update_git_settings(test_params)

    # Verify updates
    git_settings = await service.list_org_settings(keys=GitSettingsUpdate.keys())
    settings_dict = {
        setting.key: service.get_value(setting) for setting in git_settings
    }
    assert settings_dict["git_allowed_domains"] == ["github.com", "gitlab.com"]
    assert settings_dict["git_repo_url"] == "https://github.com/test/repo"
    assert settings_dict["git_repo_package_name"] == "test-package"


@pytest.mark.anyio
async def test_update_saml_settings(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Test updating SAML settings."""
    service = settings_service_with_defaults

    test_params = SAMLSettingsUpdate(
        saml_enabled=True,
        saml_idp_metadata_url="https://test-idp.com",
        saml_sp_acs_url="https://test-sp.com",
    )
    await service.update_saml_settings(test_params)

    saml_settings = await service.list_org_settings(keys=SAMLSettingsUpdate.keys())
    settings_dict = {
        setting.key: service.get_value(setting) for setting in saml_settings
    }
    assert settings_dict["saml_enabled"] is True
    assert settings_dict["saml_idp_metadata_url"] == "https://test-idp.com"
    assert settings_dict["saml_sp_acs_url"] == "https://test-sp.com"


@pytest.mark.anyio
async def test_update_auth_settings(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Test updating authentication settings."""
    service = settings_service_with_defaults

    test_params = AuthSettingsUpdate(
        auth_basic_enabled=True,
        auth_require_email_verification=True,
        auth_allowed_email_domains={"test.com"},
        auth_min_password_length=16,
        auth_session_expire_time_seconds=3600,
    )
    await service.update_auth_settings(test_params)

    auth_settings = await service.list_org_settings(keys=AuthSettingsUpdate.keys())
    settings_dict = {
        setting.key: service.get_value(setting) for setting in auth_settings
    }
    assert settings_dict["auth_basic_enabled"] is True
    assert settings_dict["auth_require_email_verification"] is True
    assert settings_dict["auth_allowed_email_domains"] == ["test.com"]  # Returns a list
    assert settings_dict["auth_min_password_length"] == 16
    assert settings_dict["auth_session_expire_time_seconds"] == 3600


@pytest.mark.anyio
async def test_update_oauth_settings(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Test updating OAuth settings."""
    service = settings_service_with_defaults

    test_params = OAuthSettingsUpdate(oauth_google_enabled=True)
    await service.update_oauth_settings(test_params)

    oauth_settings = await service.list_org_settings(keys=OAuthSettingsUpdate.keys())
    settings_dict = {
        setting.key: service.get_value(setting) for setting in oauth_settings
    }
    assert settings_dict["oauth_google_enabled"] is True


@pytest.mark.anyio
async def test_get_setting_shorthand(
    settings_service: SettingsService,
    create_params: SettingCreate,
    svc_admin_role: Role,
) -> None:
    """Test the get_setting shorthand function with and without roles."""
    token = ctx_role.set(None)  # type: ignore
    assert ctx_role.get() is None, "Role should be cleared"
    try:
        # Create a test setting first
        curr_session = settings_service.session

        created_setting = await settings_service.create_org_setting(create_params)

        # Test with valid role (should return value)
        value = await get_setting(
            created_setting.key, role=svc_admin_role, session=curr_session
        )
        assert value == create_params.value

        # Test with no role (should return None)
        no_role_value = await get_setting(
            created_setting.key, role=None, session=curr_session
        )
        assert no_role_value is None

        # Test retrieving non-existent setting
        nonexistent_value = await get_setting(
            "nonexistent-key", role=svc_admin_role, session=curr_session
        )
        assert nonexistent_value is None
    finally:
        ctx_role.set(token)  # type: ignore


@pytest.mark.anyio
async def test_check_other_auth_enabled_success(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Test check_other_auth_enabled when another auth type is enabled."""
    service = settings_service_with_defaults

    # Enable both SAML and Basic auth
    await service.update_saml_settings(SAMLSettingsUpdate(saml_enabled=True))
    await service.update_auth_settings(AuthSettingsUpdate(auth_basic_enabled=True))

    # Should not raise an exception when checking SAML (since Basic is enabled)
    from tracecat.auth.enums import AuthType
    from tracecat.settings.router import check_other_auth_enabled

    await check_other_auth_enabled(service, AuthType.SAML)


@pytest.mark.anyio
async def test_check_other_auth_enabled_failure(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Test check_other_auth_enabled when no other auth type is enabled."""
    service = settings_service_with_defaults

    # Disable all auth types except Basic
    await service.update_saml_settings(SAMLSettingsUpdate(saml_enabled=False))
    await service.update_oauth_settings(OAuthSettingsUpdate(oauth_google_enabled=False))
    await service.update_auth_settings(AuthSettingsUpdate(auth_basic_enabled=True))

    # Should raise HTTPException when trying to disable the last enabled auth type
    with pytest.raises(HTTPException) as exc_info:
        await check_other_auth_enabled(service, AuthType.BASIC)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "At least one other auth type must be enabled"
