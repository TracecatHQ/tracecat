from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class BaseSettingsGroup(BaseModel):
    """Base class for configurable settings."""

    @classmethod
    def keys(cls, *, exclude: set[str] | None = None) -> set[str]:
        """Get the setting keys as a set."""
        all_keys = set(cls.model_fields.keys())
        if exclude:
            all_keys -= exclude
        return all_keys


class GitSettingsRead(BaseSettingsGroup):
    git_allowed_domains: list[str]
    git_repo_url: str | None = Field(default=None)
    git_repo_package_name: str | None = Field(default=None)


class GitSettingsUpdate(BaseSettingsGroup):
    git_allowed_domains: list[str] = Field(
        default_factory=lambda: ["github.com", "gitlab.com", "bitbucket.org"],
        description="Allowed git domains for authentication.",
    )
    git_repo_url: str | None = Field(default=None)
    git_repo_package_name: str | None = Field(default=None)


class SAMLSettingsRead(BaseSettingsGroup):
    saml_enabled: bool
    saml_enforced: bool
    saml_idp_metadata_url: str | None = Field(default=None)
    saml_sp_acs_url: str  # Read only

    @field_validator("saml_enforced", mode="before")
    @classmethod
    def validate_saml_enforced(cls, value: bool, info: ValidationInfo) -> bool:
        """Validate that SAML enforcement requires SAML to be enabled."""
        if value and not info.data.get("saml_enabled", False):
            raise ValueError("SAML must be enabled to enforce SAML authentication")
        return value


class SAMLSettingsUpdate(BaseSettingsGroup):
    saml_enabled: bool = Field(default=True, description="Whether SAML is enabled.")
    saml_enforced: bool = Field(
        default=False,
        description="Whether SAML is enforced. If true, users can only use SAML to authenticate."
        " Requires SAML to be enabled.",
    )
    saml_idp_metadata_url: str | None = Field(default=None)


class AuthSettingsRead(BaseSettingsGroup):
    auth_basic_enabled: bool
    auth_require_email_verification: bool
    auth_allowed_email_domains: list[str]
    auth_min_password_length: int
    auth_session_expire_time_seconds: int


class AuthSettingsUpdate(BaseSettingsGroup):
    auth_basic_enabled: bool = Field(
        default=True,
        description="Whether basic auth is enabled.",
    )
    auth_require_email_verification: bool = Field(
        default=False,
        description="Whether email verification is required for authentication.",
    )
    auth_allowed_email_domains: list[str] = Field(
        default_factory=list,
        description="Allowed email domains for authentication. If empty, all domains are allowed.",
    )
    auth_min_password_length: int = Field(
        default=12,
        description="Minimum password length for authentication.",
    )
    auth_session_expire_time_seconds: int = Field(
        default=86400 * 7,  # 1 week
        description="Session expiration time in seconds.",
    )


class OAuthSettingsRead(BaseSettingsGroup):
    """Settings for OAuth authentication."""

    oauth_google_enabled: bool


class OAuthSettingsUpdate(BaseSettingsGroup):
    """Settings for OAuth authentication."""

    oauth_google_enabled: bool = Field(
        default=True, description="Whether OAuth is enabled."
    )


class AppSettingsRead(BaseSettingsGroup):
    """Settings for the app."""

    app_registry_validation_enabled: bool
    app_executions_query_limit: int
    app_interactions_enabled: bool
    app_workflow_export_enabled: bool


class AppSettingsUpdate(BaseSettingsGroup):
    """Settings for OAuth authentication."""

    app_registry_validation_enabled: bool = Field(
        default=False, description="Whether registry validation is enabled."
    )
    app_executions_query_limit: int = Field(
        default=100,
        description="The maximum number of executions to return in a single query.",
    )
    app_interactions_enabled: bool = Field(
        default=False,
        description="Whether app interactions are enabled.",
    )
    app_workflow_export_enabled: bool = Field(
        default=True,
        description="Whether workflow exports are enabled.",
    )


class ValueType(StrEnum):
    # This is the default type
    JSON = "json"
    """A physical JSON value"""
    # Add custom types that map to particular pydantic models for more complex types


class SettingUpdate(BaseModel):
    """Update a setting. Note that we don't allow updating the key and encryption status."""

    value_type: ValueType | None = None
    value: Any | None = None


class SettingCreate(BaseModel):
    key: str
    value_type: ValueType = ValueType.JSON
    value: Any
    is_sensitive: bool = Field(
        description="Whether the setting is sensitive. Once set, it cannot be changed."
    )
