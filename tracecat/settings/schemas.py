from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from tracecat.agent.otel_config import AgentOtelConfig, validate_otel_header_items
from tracecat.auth.ip_allowlist import (
    IP_ALLOWLIST_DESCRIPTION_MAX_LENGTH,
    IP_ALLOWLIST_MAX_CIDRS_PER_LIST,
    IP_ALLOWLIST_MAX_ENTRIES,
    IP_ALLOWLIST_NAME_MAX_LENGTH,
    normalize_cidrs,
)
from tracecat.git.constants import GIT_SSH_URL_REGEX
from tracecat.identifiers import WorkspaceID


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

    @field_validator("git_repo_url", mode="before")
    def validate_git_repo_url(cls, value: str | None) -> str | None:
        """Validate that git_repo_url is a valid Git SSH URL if provided."""
        if value is None:
            return value

        # Use shared regex from git utils to ensure consistency across the codebase
        if not GIT_SSH_URL_REGEX.match(value):
            raise ValueError(
                "Must be a valid Git SSH URL (e.g., git+ssh://<user>@github.com/org/repo.git)"
            )

        return value


class SAMLSettingsRead(BaseSettingsGroup):
    saml_enabled: bool
    saml_enforced: bool
    saml_idp_metadata_url: str | None = Field(default=None)
    saml_sp_acs_url: str  # Read only
    decryption_failed_keys: list[str] = Field(
        default_factory=list,
        description=(
            "Encrypted setting keys that could not be decrypted with the current "
            "encryption key and must be reconfigured."
        ),
    )

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


class AppSettingsRead(BaseSettingsGroup):
    """Settings for the app."""

    app_registry_validation_enabled: bool
    app_executions_query_limit: int
    app_interactions_enabled: bool
    app_workflow_export_enabled: bool
    app_create_workspace_on_register: bool
    app_action_form_mode_enabled: bool
    app_unsafe_disable_secret_error_withholding_workspace_ids: list[WorkspaceID] = (
        Field(default_factory=list)
    )
    app_unsafe_disable_secret_error_withholding_break_glass_workspace_ids: list[
        WorkspaceID
    ] = Field(default_factory=list)


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
    app_create_workspace_on_register: bool = Field(
        default=False,
        description="Whether to automatically create a workspace when a user signs up.",
    )
    app_action_form_mode_enabled: bool = Field(
        default=True,
        description="Whether to enable form mode for action inputs. When disabled, only YAML mode is available, preserving raw YAML formatting.",
    )
    app_unsafe_disable_secret_error_withholding_workspace_ids: list[WorkspaceID] = (
        Field(
            default_factory=list,
            description=(
                "UNSAFE: workspaces whose actions may opt into showing their "
                "original error message when secrets are in scope. Each action "
                "must still enable 'Show error details' individually. Known "
                "secret values are still masked."
            ),
        )
    )
    app_unsafe_disable_secret_error_withholding_break_glass_workspace_ids: list[
        WorkspaceID
    ] = Field(
        default_factory=list,
        description=(
            "UNSAFE (break glass): workspaces where secret error withholding is "
            "disabled for every action, without a per-action opt-in. Known "
            "secret values are still masked. Intended for temporary debugging; "
            "remove the workspace once done."
        ),
    )


class AuditSettingsRead(BaseSettingsGroup):
    """Settings for audit logging."""

    audit_webhook_url: str | None
    audit_webhook_custom_headers: dict[str, str] | None = None
    audit_webhook_custom_payload: dict[str, Any] | None = None
    audit_webhook_payload_attribute: str | None = None
    audit_webhook_verify_ssl: bool = True
    decryption_failed_keys: list[str] = Field(
        default_factory=list,
        description=(
            "Encrypted setting keys that could not be decrypted with the current "
            "encryption key and must be reconfigured."
        ),
    )


class AuditSettingsUpdate(BaseSettingsGroup):
    """Settings for audit logging."""

    audit_webhook_url: str | None = Field(
        default=None,
        description="Webhook URL that receives streamed audit events. When unset, audit events are skipped.",
    )
    audit_webhook_custom_headers: dict[str, str] | None = Field(
        default=None,
        description="Custom headers to include in audit webhook requests. Header names are case-insensitive.",
    )
    audit_webhook_custom_payload: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Custom JSON fields merged into streamed audit event payloads. "
            "Canonical audit event fields take precedence; conflicting custom "
            "keys are ignored."
        ),
    )
    audit_webhook_payload_attribute: str | None = Field(
        default=None,
        description=(
            "Optional wrapper key for audit payloads. When set to a value like "
            "'event', payload is sent as {'event': <audit_payload>}."
        ),
    )
    audit_webhook_verify_ssl: bool = Field(
        default=True,
        description=(
            "Whether TLS certificates are verified for webhook requests. "
            "Disable only for trusted on-prem/self-signed endpoints."
        ),
    )


AuditWebhookTestErrorCategory = Literal[
    "receiver_error",
    "timeout",
    "request_error",
]


class AuditWebhookTestResult(BaseModel):
    """Result of a synchronous audit webhook test-fire request."""

    ok: bool
    receiver_status_code: int | None = None
    error_category: AuditWebhookTestErrorCategory | None = None


class IPAllowlist(BaseModel):
    """A named group of allowed IP addresses or CIDR ranges."""

    name: str = Field(
        min_length=1,
        max_length=IP_ALLOWLIST_NAME_MAX_LENGTH,
        description="Human-readable name, e.g. 'Corporate VPN'.",
    )
    description: str | None = Field(
        default=None,
        max_length=IP_ALLOWLIST_DESCRIPTION_MAX_LENGTH,
        description="Optional note on what this allowlist covers and who owns it.",
    )
    cidrs: list[str] = Field(
        min_length=1,
        max_length=IP_ALLOWLIST_MAX_CIDRS_PER_LIST,
        description="IPv4 or IPv6 addresses or CIDR ranges.",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not (stripped := value.strip()):
            raise ValueError("Name cannot be blank")
        return stripped

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("cidrs")
    @classmethod
    def validate_cidrs(cls, value: list[str]) -> list[str]:
        try:
            normalized = normalize_cidrs(value)
        except ValueError as e:
            raise ValueError(f"Invalid IP address or CIDR range: {e}") from e
        if not normalized:
            raise ValueError("At least one IP address or CIDR range is required")
        return normalized


_IP_ALLOWLISTS_ADAPTER = TypeAdapter(list[IPAllowlist])


def parse_stored_ip_allowlists(value: object) -> list[IPAllowlist]:
    """Decode the persisted ``ip_allowlists`` setting value.

    Malformed stored data yields an empty list rather than an error so a bad
    row can never break authentication.
    """
    try:
        return _IP_ALLOWLISTS_ADAPTER.validate_python(value)
    except ValidationError:
        return []


def ip_allowlist_cidrs(allowlists: list[IPAllowlist]) -> list[str]:
    """Flatten allowlists into the CIDR strings used for matching."""
    return [cidr for allowlist in allowlists for cidr in allowlist.cidrs]


class SecuritySettingsRead(BaseSettingsGroup):
    """Organization security settings."""

    ip_allowlist_enabled: bool
    ip_allowlists: list[IPAllowlist]


class SecuritySettingsUpdate(BaseSettingsGroup):
    """Organization security settings."""

    ip_allowlist_enabled: bool = Field(
        default=False,
        description=(
            "Restrict organization API access to the configured IP allowlists. "
            "Has no effect while no allowlists exist."
        ),
    )
    ip_allowlists: list[IPAllowlist] = Field(
        default_factory=list,
        max_length=IP_ALLOWLIST_MAX_ENTRIES,
        description="Named groups of allowed IP addresses or CIDR ranges.",
    )

    @field_validator("ip_allowlists")
    @classmethod
    def validate_ip_allowlists(cls, value: list[IPAllowlist]) -> list[IPAllowlist]:
        names = [allowlist.name.casefold() for allowlist in value]
        if len(names) != len(set(names)):
            raise ValueError("Allowlist names must be unique")
        return value

    @property
    def cidrs(self) -> list[str]:
        return ip_allowlist_cidrs(self.ip_allowlists)


class IPAllowlistCheckRequest(BaseModel):
    """Check whether an IP address would be admitted by the saved allowlist."""

    ip_address: str = Field(min_length=1, max_length=45)


class IPAllowlistCheckResult(BaseModel):
    allowed: bool
    matched_cidr: str | None = None
    matched_allowlist: str | None = None
    """Name of the allowlist containing ``matched_cidr``."""
    enforced: bool
    """Whether the allowlist is currently enabled and non-empty."""


class AgentSettingsRead(BaseSettingsGroup):
    agent_default_model: str | None
    agent_fixed_args: str | None
    agent_case_chat_prompt: str
    agent_case_chat_inject_content: bool


class AgentSettingsUpdate(BaseSettingsGroup):
    agent_default_model: str | None = Field(
        default=None,
        description="The default AI model to use for agent operations.",
    )
    agent_fixed_args: str | None = Field(
        default=None,
        min_length=1,
        max_length=10000,
        description="Fixed arguments for agent tools as a JSON string. Format: {'tool_name': {'arg': 'value'}}",
    )
    agent_case_chat_prompt: str = Field(
        default="",
        description="Additional instructions for case chat agent; prepended to UI-provided instructions.",
    )
    agent_case_chat_inject_content: bool = Field(
        default=False,
        description="Whether to automatically inject case content into agent prompts when a case_id is available.",
    )


class AgentOtelSettingsRead(BaseSettingsGroup):
    agent_otel_config: AgentOtelConfig = Field(default_factory=AgentOtelConfig)


class AgentOtelSettingsUpdate(BaseSettingsGroup):
    agent_otel_config: AgentOtelConfig = Field(
        default_factory=AgentOtelConfig,
        description="Claude Code OTel telemetry configuration for agent runs.",
    )
    agent_otel_headers: dict[str, str] | None = Field(
        default=None,
        description=(
            "Encrypted headers for the Claude Code OTLP exporter. Omitted values "
            "leave existing headers unchanged."
        ),
    )

    @field_validator("agent_otel_headers", mode="before")
    @classmethod
    def validate_agent_otel_headers(cls, value: Any) -> Any:
        if value is None or not isinstance(value, dict):
            return value
        validate_otel_header_items(cast(dict[str, Any], value))
        return value


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
