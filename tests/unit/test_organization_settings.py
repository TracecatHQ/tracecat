from typing import Any

import orjson
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.enums import AuthType
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import OrganizationDomain
from tracecat.organization.domains import normalize_domain
from tracecat.settings.constants import SENSITIVE_SETTINGS_KEYS
from tracecat.settings.router import (
    check_other_auth_enabled,
    check_saml_domain_prerequisites,
)
from tracecat.settings.schemas import (
    AuditSettingsUpdate,
    GitSettingsUpdate,
    SAMLSettingsUpdate,
    SettingCreate,
    SettingUpdate,
    ValueType,
)
from tracecat.settings.service import SettingsService, get_setting, get_setting_override

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
    """Test updating Git settings with valid SSH URL."""
    # Update Git settings with allowed domains and repo info
    service = settings_service_with_defaults
    test_params = GitSettingsUpdate(
        git_allowed_domains=["github.com", "gitlab.com"],
        git_repo_url="git+ssh://git@github.com/test/repo.git",
        git_repo_package_name="test-package",
    )
    await service.update_git_settings(test_params)

    # Verify updates
    git_settings = await service.list_org_settings(keys=GitSettingsUpdate.keys())
    settings_dict = {
        setting.key: service.get_value(setting) for setting in git_settings
    }
    assert settings_dict["git_allowed_domains"] == ["github.com", "gitlab.com"]
    assert settings_dict["git_repo_url"] == "git+ssh://git@github.com/test/repo.git"
    assert settings_dict["git_repo_package_name"] == "test-package"


@pytest.mark.anyio
async def test_update_audit_settings(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Ensure audit webhook updates persist."""
    service = settings_service_with_defaults
    await service.update_audit_settings(
        AuditSettingsUpdate(audit_webhook_url="https://example.com/audit")
    )
    settings = await service.list_org_settings(keys={"audit_webhook_url"})
    settings_dict = {setting.key: service.get_value(setting) for setting in settings}
    assert settings_dict["audit_webhook_url"] == "https://example.com/audit"


@pytest.mark.anyio
async def test_update_audit_settings_can_clear(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Ensure audit webhook can be unset."""
    service = settings_service_with_defaults
    await service.update_audit_settings(
        AuditSettingsUpdate(audit_webhook_url="https://example.com/audit")
    )
    await service.update_audit_settings(AuditSettingsUpdate(audit_webhook_url=None))
    settings = await service.list_org_settings(keys={"audit_webhook_url"})
    settings_dict = {setting.key: service.get_value(setting) for setting in settings}
    assert settings_dict["audit_webhook_url"] is None


@pytest.mark.anyio
async def test_update_audit_custom_headers_encrypted_at_rest(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Ensure custom headers are stored encrypted and round-trip correctly."""
    service = settings_service_with_defaults
    custom_headers = {
        "Authorization": "Bearer super-secret-token",
        "X-Tracecat-Source": "audit",
    }
    await service.update_audit_settings(
        AuditSettingsUpdate(audit_webhook_custom_headers=custom_headers)
    )

    setting = await service.get_org_setting("audit_webhook_custom_headers")
    assert setting is not None
    assert setting.is_encrypted is True
    assert service.get_value(setting) == custom_headers
    assert setting.value != orjson.dumps(custom_headers, option=orjson.OPT_SORT_KEYS)


@pytest.mark.anyio
async def test_update_audit_custom_payload_encrypted_at_rest(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Ensure custom payload is stored encrypted and round-trip correctly."""
    service = settings_service_with_defaults
    custom_payload = {
        "event_type": "tracecat.audit",
        "metadata": {"env": "staging"},
    }
    await service.update_audit_settings(
        AuditSettingsUpdate(audit_webhook_custom_payload=custom_payload)
    )

    setting = await service.get_org_setting("audit_webhook_custom_payload")
    assert setting is not None
    assert setting.is_encrypted is True
    assert service.get_value(setting) == custom_payload
    assert setting.value != orjson.dumps(custom_payload, option=orjson.OPT_SORT_KEYS)


@pytest.mark.anyio
async def test_update_audit_verify_ssl_setting(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Ensure SSL verification toggle is persisted for audit webhook delivery."""
    service = settings_service_with_defaults
    await service.update_audit_settings(
        AuditSettingsUpdate(audit_webhook_verify_ssl=False)
    )

    settings = await service.list_org_settings(keys={"audit_webhook_verify_ssl"})
    settings_dict = {setting.key: service.get_value(setting) for setting in settings}
    assert settings_dict["audit_webhook_verify_ssl"] is False


@pytest.mark.anyio
async def test_update_audit_payload_attribute_setting(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Ensure payload wrapper attribute is persisted for audit webhook delivery."""
    service = settings_service_with_defaults
    await service.update_audit_settings(
        AuditSettingsUpdate(audit_webhook_payload_attribute="event")
    )

    settings = await service.list_org_settings(keys={"audit_webhook_payload_attribute"})
    settings_dict = {setting.key: service.get_value(setting) for setting in settings}
    assert settings_dict["audit_webhook_payload_attribute"] == "event"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "valid_url",
    [
        "git+ssh://git@github.com/org/repo.git",
        "git+ssh://git@gitlab.example.com:2222/org/repo.git",
        "git+ssh://git@gitlab.com/org/team/subteam/repo.git",
        "git+ssh://git@github.com/org/repo.git@main",
        "git+ssh://git@github.com/org/repo",  # Without .git suffix
        "git+ssh://git@example.com/very/deep/nested/org/repo.git",
    ],
)
async def test_git_settings_valid_ssh_urls(
    settings_service_with_defaults: SettingsService,
    valid_url: str,
) -> None:
    """Test that valid Git SSH URLs are accepted."""
    service = settings_service_with_defaults

    # This should not raise an exception
    test_params = GitSettingsUpdate(git_repo_url=valid_url)
    await service.update_git_settings(test_params)

    # Verify the URL was saved
    git_settings = await service.list_org_settings(keys={"git_repo_url"})
    settings_dict = {
        setting.key: service.get_value(setting) for setting in git_settings
    }
    assert settings_dict["git_repo_url"] == valid_url


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid_url",
    [
        "https://github.com/org/repo.git",  # Wrong protocol
        "git+ssh://user@github.com/org/repo.git",  # Wrong user
        "git+ssh://git@github.com/",  # No path
        "git+ssh://git@/org/repo.git",  # No host
        "git://git@github.com/org/repo.git",  # Missing +ssh
        "not-a-url",  # Not a URL at all
        "",  # Empty string
        "git+ssh://git@github.com:not_a_port/org/repo.git",  # Non numeric port
        "git+ssh://git@github.com:/org/repo.git",  # Missing port after colon
        "git+ssh://git@github.com/repo.git",  # Missing org segment
    ],
)
async def test_git_settings_invalid_ssh_urls(
    settings_service_with_defaults: SettingsService,
    invalid_url: str,
) -> None:
    """Test that invalid Git SSH URLs are rejected."""
    from pydantic import ValidationError

    # This should raise a ValidationError
    with pytest.raises(ValidationError) as exc_info:
        GitSettingsUpdate(git_repo_url=invalid_url)

    # Verify the error message mentions Git SSH URL
    error_detail = str(exc_info.value)
    assert "Must be a valid Git SSH URL" in error_detail


@pytest.mark.anyio
async def test_git_settings_null_url_allowed(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Test that None/null git_repo_url is allowed."""
    service = settings_service_with_defaults

    # This should not raise an exception
    test_params = GitSettingsUpdate(git_repo_url=None)
    await service.update_git_settings(test_params)

    # Verify None was saved
    git_settings = await service.list_org_settings(keys={"git_repo_url"})
    settings_dict = {
        setting.key: service.get_value(setting) for setting in git_settings
    }
    assert settings_dict["git_repo_url"] is None


@pytest.mark.anyio
async def test_update_saml_settings(
    settings_service_with_defaults: SettingsService,
) -> None:
    """Test updating SAML settings."""
    service = settings_service_with_defaults

    test_params = SAMLSettingsUpdate(
        saml_enabled=True,
        saml_idp_metadata_url="https://test-idp.com",
    )
    await service.update_saml_settings(test_params)

    saml_settings = await service.list_org_settings(keys=SAMLSettingsUpdate.keys())
    settings_dict = {
        setting.key: service.get_value(setting) for setting in saml_settings
    }
    assert settings_dict["saml_enabled"] is True
    assert settings_dict["saml_idp_metadata_url"] == "https://test-idp.com"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "setting_key",
    [
        "saml_idp_metadata_url",
        "audit_webhook_url",
        "audit_webhook_custom_headers",
        "audit_webhook_custom_payload",
    ],
)
async def test_get_values_with_decryption_fallback_for_invalid_encrypted_settings(
    settings_service_with_defaults: SettingsService,
    setting_key: str,
) -> None:
    """Invalid encrypted values should not crash grouped settings reads."""
    service = settings_service_with_defaults

    setting = await service.get_org_setting(setting_key)
    assert setting is not None
    assert setting.is_encrypted is True
    setting.value = b"invalid-ciphertext"
    service.session.add(setting)
    await service.session.commit()

    settings = await service.list_org_settings(keys={setting_key})
    settings_dict, decryption_failed_keys = service.get_values_with_decryption_fallback(
        settings
    )

    assert settings_dict[setting_key] is None
    assert decryption_failed_keys == [setting_key]


@pytest.mark.anyio
async def test_get_setting_shorthand(
    settings_service: SettingsService,
    create_params: SettingCreate,
    svc_admin_role: Role,
) -> None:
    """Test the get_setting shorthand function with roles and defaults."""
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

        # Test retrieving non-existent setting with default
        default_value = {"default": "value"}
        nonexistent_with_default = await get_setting(
            "nonexistent-key",
            role=svc_admin_role,
            session=curr_session,
            default=default_value,
        )
        assert nonexistent_with_default == default_value

        # Test retrieving non-existent setting without default
        nonexistent_no_default = await get_setting(
            "nonexistent-key", role=svc_admin_role, session=curr_session
        )
        assert nonexistent_no_default is None
    finally:
        ctx_role.reset(token)  # type: ignore


@pytest.mark.anyio
async def test_check_other_auth_enabled_success(
    settings_service_with_defaults: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test check_other_auth_enabled when another auth type is enabled."""
    service = settings_service_with_defaults
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.SAML, AuthType.BASIC})

    await check_other_auth_enabled(service, AuthType.SAML)


@pytest.mark.anyio
async def test_check_other_auth_enabled_failure(
    settings_service_with_defaults: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test check_other_auth_enabled when no other auth type is enabled."""
    service = settings_service_with_defaults
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.SAML})

    # Should raise HTTPException when trying to disable the last enabled auth type
    with pytest.raises(HTTPException) as exc_info:
        await check_other_auth_enabled(service, AuthType.SAML)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "At least one other auth type must be enabled"


@pytest.mark.anyio
async def test_check_saml_domain_prerequisites_raises_without_domains(
    settings_service_with_defaults: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = settings_service_with_defaults
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    with pytest.raises(HTTPException) as exc_info:
        await check_saml_domain_prerequisites(
            session=service.session,
            role=service.role,
            params=SAMLSettingsUpdate(saml_enabled=True),
        )

    assert exc_info.value.status_code == 400
    assert "active organization domain" in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_check_saml_domain_prerequisites_passes_with_active_domain(
    settings_service_with_defaults: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = settings_service_with_defaults
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    normalized = normalize_domain("acme.example")
    service.session.add(
        OrganizationDomain(
            organization_id=service.organization_id,
            domain=normalized.domain,
            normalized_domain=normalized.normalized_domain,
            is_primary=True,
            is_active=True,
        )
    )
    await service.session.commit()

    await check_saml_domain_prerequisites(
        session=service.session,
        role=service.role,
        params=SAMLSettingsUpdate(saml_enabled=True),
    )


@pytest.mark.anyio
async def test_check_saml_domain_prerequisites_ignores_non_enabling_updates(
    settings_service_with_defaults: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = settings_service_with_defaults
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    await check_saml_domain_prerequisites(
        session=service.session,
        role=service.role,
        params=SAMLSettingsUpdate(saml_idp_metadata_url="https://idp.example.com"),
    )


@pytest.mark.anyio
async def test_check_saml_domain_prerequisites_skips_single_tenant_without_domains(
    settings_service_with_defaults: SettingsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = settings_service_with_defaults
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)

    await check_saml_domain_prerequisites(
        session=service.session,
        role=service.role,
        params=SAMLSettingsUpdate(saml_enabled=True),
    )


@pytest.mark.anyio
async def test_init_default_settings(
    settings_service: SettingsService,
) -> None:
    """Test that default settings are initialized correctly."""
    # Execute
    await settings_service.init_default_settings()

    # Verify all settings were created
    expected_keys = {key for group in settings_service.groups for key in group.keys()}

    settings = await settings_service.list_org_settings(keys=expected_keys)
    created_keys = {setting.key for setting in settings}

    # Check all expected keys were created
    assert created_keys == expected_keys

    # Verify sensitive settings are encrypted
    sensitive_settings = [s for s in settings if s.key in SENSITIVE_SETTINGS_KEYS]
    assert all(s.is_encrypted for s in sensitive_settings)

    # Get the expected defaults
    defaults = {key: value for cls in settings_service.groups for key, value in cls()}
    for setting in settings:
        value = settings_service.get_value(setting)
        assert value == defaults[setting.key]

    # Test idempotency - running again shouldn't create duplicates
    await settings_service.init_default_settings()
    settings_after = await settings_service.list_org_settings(keys=expected_keys)
    assert len(settings) == len(settings_after)


@pytest.mark.parametrize(
    "key,env_value,expected",
    [
        ("saml_enabled", "true", "true"),
        ("unauthorized_setting", "true", None),
    ],
)
def test_get_setting_override(
    key: str, env_value: str, expected: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test environment variable overrides for settings."""
    if expected is not None:
        monkeypatch.setenv(f"TRACECAT__SETTING_OVERRIDE_{key.upper()}", env_value)

    assert get_setting_override(key) == expected, f"Expected {expected} for {key}"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "key,env_value,expected",
    [
        ("saml_enabled", "true", True),
        ("unauthorized_setting", "true", None),
        ("saml_enabled", "some_string", "some_string"),
    ],
)
async def test_setting_with_override(
    settings_service: SettingsService,
    svc_admin_role: Role,
    key: str,
    env_value: str,
    expected: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_setting with environment overrides."""
    if key == "saml_enabled":
        monkeypatch.setenv(f"TRACECAT__SETTING_OVERRIDE_{key.upper()}", env_value)

    # Test with both session and role
    value = await get_setting(
        key,
        role=svc_admin_role,
        session=settings_service.session,
    )
    assert value == expected

    # Test with default value when no override
    default_value = {"test": "default"}
    no_override_value = await get_setting(
        "nonexistent_key",
        role=svc_admin_role,
        session=settings_service.session,
        default=default_value,
    )
    assert no_override_value == default_value
