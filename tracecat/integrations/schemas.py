"""Database models for user integrations with external services.

Terminology:
- Integration: A user's integration with an external service.
- Provider: An external service that can be integrated with. Defined by BaseOAuthProvider.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Self, TypedDict
from urllib.parse import urlparse

from pydantic import (
    UUID4,
    BaseModel,
    Discriminator,
    Field,
    SecretStr,
    Tag,
    computed_field,
    field_validator,
)

from tracecat.identifiers import UserID, WorkspaceID
from tracecat.integrations.enums import IntegrationStatus, MCPAuthType, OAuthGrantType
from tracecat.integrations.types import MCPServerType


# Pydantic models for API responses
class IntegrationReadMinimal(BaseModel):
    """Response model for user integration."""

    id: UUID4
    provider_id: str
    status: IntegrationStatus
    is_expired: bool


class IntegrationRead(BaseModel):
    """Response model for user integration."""

    # Core identification and timestamps
    id: UUID4
    created_at: datetime
    updated_at: datetime
    user_id: UserID | None = None

    # Provider information
    provider_id: str
    authorization_endpoint: str | None = Field(
        default=None,
        description="OAuth authorization endpoint configured for this integration.",
    )
    token_endpoint: str | None = Field(
        default=None,
        description="OAuth token endpoint configured for this integration.",
    )

    # OAuth token details
    token_type: str
    expires_at: datetime | None

    # OAuth credentials
    client_id: str | None = Field(
        default=None,
        description="OAuth client ID for the provider",
    )

    # OAuth scopes
    granted_scopes: list[str] | None = Field(
        default=None,
        description="OAuth scopes granted for this integration",
    )
    requested_scopes: list[str] | None = Field(
        default=None,
        description="OAuth scopes requested by user for this integration",
    )

    # Integration state
    status: IntegrationStatus
    is_expired: bool


class IntegrationUpdate(BaseModel):
    """Request model for updating an integration."""

    # Additional identifier
    grant_type: OAuthGrantType = Field(
        ...,
        description="OAuth grant type for this integration",
    )

    # Updateable fields
    client_id: str | None = Field(
        default=None,
        description="OAuth client ID for the provider",
        min_length=1,
    )
    client_secret: SecretStr | None = Field(
        default=None,
        description="OAuth client secret for the provider",
        min_length=1,
    )
    authorization_endpoint: str | None = Field(
        default=None,
        description="OAuth authorization endpoint URL. Overrides provider defaults when set.",
        min_length=8,
    )
    token_endpoint: str | None = Field(
        default=None,
        description="OAuth token endpoint URL. Overrides provider defaults when set.",
        min_length=8,
    )
    scopes: list[str] | None = Field(
        default=None,
        description="OAuth scopes to request for this integration",
    )

    @field_validator("authorization_endpoint", "token_endpoint", mode="before")
    @classmethod
    def _validate_https_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
        if not value:
            return None
        parsed = urlparse(value)
        if parsed.scheme.lower() != "https":
            raise ValueError("OAuth endpoints must use HTTPS")
        if not parsed.netloc:
            raise ValueError("OAuth endpoints must include a hostname")
        return value


class CustomOAuthProviderBase(BaseModel):
    """Shared fields for custom OAuth provider definitions."""

    name: str = Field(..., min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=512)
    grant_type: OAuthGrantType
    authorization_endpoint: str = Field(
        ..., description="OAuth authorization endpoint URL", min_length=8
    )
    token_endpoint: str = Field(
        ..., description="OAuth token endpoint URL", min_length=8
    )
    scopes: list[str] | None = Field(
        default=None, description="Default OAuth scopes to request"
    )

    @field_validator("authorization_endpoint", "token_endpoint", mode="before")
    @classmethod
    def _validate_https_endpoint(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Endpoint is required")
        if isinstance(value, str):
            value = value.strip()
        if not value:
            raise ValueError("Endpoint must not be empty")
        parsed = urlparse(value)
        if parsed.scheme.lower() != "https":
            raise ValueError("OAuth endpoints must use HTTPS")
        if not parsed.netloc:
            raise ValueError("OAuth endpoints must include a hostname")
        return value


class CustomOAuthProviderCreate(CustomOAuthProviderBase):
    """Request payload for creating a custom OAuth provider."""

    provider_id: str | None = Field(
        default=None,
        min_length=3,
        max_length=255,
        description="Optional custom identifier for the provider",
    )
    client_id: str = Field(
        ..., min_length=1, max_length=512, description="OAuth client identifier"
    )
    client_secret: SecretStr | None = Field(
        default=None,
        description="OAuth client secret for the provider",
        min_length=1,
    )


class IntegrationOAuthConnect(BaseModel):
    """Request model for connecting an integration."""

    auth_url: str = Field(
        ...,
        description="The URL to redirect to for OAuth authentication",
    )
    provider_id: str = Field(
        ...,
        description="The provider that the user connected to",
    )


class IntegrationOAuthCallback(BaseModel):
    """Response for OAuth callback."""

    status: str = Field(
        default="connected",
        description="The status of the OAuth callback",
    )
    provider_id: str = Field(
        ...,
        description="The provider that the user connected to",
    )
    redirect_url: str = Field(
        ...,
        description="The URL to redirect to after the OAuth callback",
    )


class IntegrationTestConnectionResponse(BaseModel):
    """Response for testing integration connection."""

    success: bool = Field(
        ...,
        description="Whether the connection test was successful",
    )
    provider_id: str = Field(
        ...,
        description="The provider that was tested",
    )
    message: str = Field(
        ...,
        description="Message describing the test result",
    )
    error: str | None = Field(
        default=None,
        description="Error message if the test failed",
    )


class ProviderMetadata(BaseModel):
    """Metadata for a provider."""

    id: str = Field(..., description="Provider identifier")
    name: str = Field(..., description="Human-readable provider name")
    description: str = Field(..., description="Provider description")
    logo_url: str | None = Field(default=None, description="URL to provider logo")
    setup_instructions: str | None = Field(
        default=None, description="Setup instructions for the provider"
    )
    requires_config: bool = Field(
        default=False,
        description="Whether this provider requires additional configuration",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this provider is available for use",
    )
    api_docs_url: str | None = Field(
        default=None, description="URL to API documentation"
    )
    setup_guide_url: str | None = Field(default=None, description="URL to setup guide")
    troubleshooting_url: str | None = Field(
        default=None, description="URL to troubleshooting documentation"
    )


class ProviderScopes(BaseModel):
    """Scope metadata for a provider."""

    default: list[str] = Field(
        ...,
        description="Default scopes for this provider.",
    )


class OAuthState(BaseModel):
    """Data class for OAuth state."""

    workspace_id: WorkspaceID
    user_id: UserID
    state: uuid.UUID

    @classmethod
    def from_state(cls, state: str) -> Self:
        """Create an OAuthState from a state string."""
        workspace_id, user_id, state = state.split(":")
        return cls(
            workspace_id=uuid.UUID(workspace_id),
            user_id=uuid.UUID(user_id),
            state=uuid.UUID(state),
        )


class OAuthProviderKwargs(TypedDict, total=False):
    """Kwargs for OAuth providers."""

    client_id: str
    client_secret: str
    scopes: list[str] | None
    authorization_endpoint: str
    token_endpoint: str


class ProviderKey(BaseModel):
    """Key for a provider that uniquely identifies it."""

    id: str
    grant_type: OAuthGrantType

    def __str__(self) -> str:
        return f"{self.id} ({self.grant_type.value})"

    def __hash__(self) -> int:
        return hash((self.id, self.grant_type))


class ProviderSchema(BaseModel):
    """Schema for a provider."""

    json_schema: dict[str, Any]


class ProviderConfig(BaseModel):
    """Data class for integration client credentials."""

    client_id: str | None = None
    client_secret: SecretStr | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    scopes: list[str] | None = None


class ProviderReadMinimal(BaseModel):
    id: str
    name: str
    description: str
    requires_config: bool
    integration_status: IntegrationStatus
    enabled: bool
    grant_type: OAuthGrantType


class ProviderRead(BaseModel):
    grant_type: OAuthGrantType
    metadata: ProviderMetadata
    scopes: ProviderScopes
    config_schema: ProviderSchema
    integration_status: IntegrationStatus
    default_authorization_endpoint: str | None = None
    default_token_endpoint: str | None = None
    authorization_endpoint_help: str | list[str] | None = None
    token_endpoint_help: str | list[str] | None = None
    # Only applicable to AuthorizationCodeOAuthProvider
    redirect_uri: str | None = None


class _MCPIntegrationCreateBase(BaseModel):
    """Shared request fields for creating an MCP integration."""

    name: str = Field(
        ..., min_length=3, max_length=255, description="MCP integration name"
    )
    description: str | None = Field(
        default=None, max_length=512, description="Optional description"
    )
    timeout: int | None = Field(
        default=30,
        ge=1,
        le=300,
        description="Timeout in seconds",
    )
    catalog_slug: str | None = Field(
        default=None,
        max_length=255,
        description="Platform MCP catalog slug this workspace config is created from",
    )


class MCPHttpIntegrationCreate(_MCPIntegrationCreateBase):
    """Request model for creating an HTTP MCP integration."""

    server_type: Literal["http"] = Field(default="http")
    server_uri: str = Field(
        ..., description="MCP server endpoint URL (required for http type)"
    )
    auth_type: MCPAuthType = Field(
        default=MCPAuthType.NONE, description="Authentication type (for http type)"
    )
    oauth_integration_id: uuid.UUID | None = Field(
        default=None, description="OAuth integration ID (required for oauth2 auth_type)"
    )
    custom_credentials: SecretStr | None = Field(
        default=None,
        description=(
            "Custom credentials as JSON headers. Required for custom auth type; "
            "optional additional headers for OAuth2 auth type."
        ),
    )

    @field_validator("server_uri", mode="before")
    @classmethod
    def _validate_server_uri(cls, value: str | None) -> str:
        """Validate and sanitize MCP server URI."""
        if value is None:
            raise ValueError("server_uri is required for http-type servers")
        if isinstance(value, str):
            value = value.strip()
        if not value:
            raise ValueError("server_uri is required for http-type servers")

        parsed = urlparse(value)
        if not parsed.netloc:
            raise ValueError("Server URI must include a hostname")
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError("Server URI must use HTTP or HTTPS")

        return value


class MCPStdioIntegrationCreate(_MCPIntegrationCreateBase):
    """Request model for creating a stdio MCP integration."""

    server_type: Literal["stdio"] = Field(default="stdio")
    stdio_command: str = Field(
        ...,
        max_length=500,
        description="Stdio command to run for stdio-type servers (e.g., 'npx')",
    )
    stdio_args: list[str] | None = Field(
        default=None,
        description="Arguments for the stdio command (e.g., ['@modelcontextprotocol/server-github'])",
    )
    stdio_env: dict[str, str] | None = Field(
        default=None,
        description="Environment variables for stdio-type servers (can reference secrets)",
    )

    @field_validator("stdio_command", mode="before")
    @classmethod
    def _validate_stdio_command(cls, value: str | None) -> str:
        """Validate and sanitize stdio command."""
        if value is None:
            raise ValueError("stdio_command is required for stdio-type servers")
        if isinstance(value, str):
            value = value.strip()
        if not value:
            raise ValueError("stdio_command is required for stdio-type servers")
        return value


def _discriminate_mcp_integration_create_server_type(value: Any) -> str:
    """Infer MCP create server type, defaulting legacy payloads to HTTP."""
    if isinstance(value, MCPHttpIntegrationCreate):
        return "http"
    if isinstance(value, MCPStdioIntegrationCreate):
        return "stdio"
    if isinstance(value, dict):
        if "server_type" in value:
            server_type = value.get("server_type")
            if isinstance(server_type, str):
                return server_type
        if "stdio_command" in value and "server_uri" not in value:
            return "stdio"
    return "http"


type MCPIntegrationCreate = Annotated[
    Annotated[MCPHttpIntegrationCreate, Tag("http")]
    | Annotated[MCPStdioIntegrationCreate, Tag("stdio")],
    Discriminator(_discriminate_mcp_integration_create_server_type),
]


class MCPIntegrationUpdate(BaseModel):
    """Request model for updating an MCP integration."""

    name: str | None = Field(default=None, min_length=3, max_length=255)
    description: str | None = Field(default=None, max_length=512)
    server_type: MCPServerType | None = Field(
        default=None,
        description="MCP server type. Changing this clears fields from the previous type.",
    )
    # HTTP-type server fields
    server_uri: str | None = None
    auth_type: MCPAuthType | None = None
    oauth_integration_id: uuid.UUID | None = None
    custom_credentials: SecretStr | None = Field(
        default=None,
        description=(
            "Custom credentials as JSON headers. Required for custom auth type; "
            "optional additional headers for OAuth2 auth type."
        ),
    )
    # Stdio-type server fields
    stdio_command: str | None = Field(
        default=None,
        max_length=500,
        description="Stdio command to run for stdio-type servers (e.g., 'npx')",
    )
    stdio_args: list[str] | None = Field(
        default=None,
        description="Arguments for the stdio command",
    )
    stdio_env: dict[str, str] | None = Field(
        default=None,
        description="Environment variables for stdio-type servers",
    )
    # General fields
    timeout: int | None = Field(
        default=None,
        ge=1,
        le=300,
        description="Timeout in seconds",
    )

    @field_validator("server_uri", mode="before")
    @classmethod
    def _validate_server_uri(cls, value: str | None) -> str | None:
        """Validate and sanitize MCP server URI."""
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
        if not value:
            return None

        # Validate it's a valid URL
        parsed = urlparse(value)
        if not parsed.netloc:
            raise ValueError("Server URI must include a hostname")
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError("Server URI must use HTTP or HTTPS")

        return value


MCPIntegrationSource = Literal["platform", "workspace"]
"""Provenance of an MCP integration.

- ``platform``: auto-created by a platform-shipped MCP auth provider (the
  lifecycle is owned by the OAuth flow for an ``MCPAuthProvider``).
- ``workspace``: explicitly created by a workspace user via the MCP servers
  page or API.
"""


MCPConnectionTarget = Literal[
    "server_uri",
    "oauth_client",
    "http_header",
    "stdio_env",
]
# No stdio_oauth2: MCP OAuth is defined for HTTP transports only; stdio
# servers receive credentials via environment variables.
MCPConnectionKind = Literal[
    "http_oauth2",
    "http_custom",
    "http_none",
    "stdio_custom",
    "stdio_none",
]
PlatformMCPCatalogStatus = Literal["available", "coming_soon", "deprecated", "hidden"]
PlatformMCPCatalogState = Literal[
    "not_configured",
    "configured",
    "connected",
    "error",
]


class MCPConnectionCredential(BaseModel):
    """User-supplied value needed to materialize a catalog connection."""

    key: str
    label: str
    description: str
    required: bool = True
    secret: bool = True
    target: MCPConnectionTarget


class MCPPackageOption(BaseModel):
    """Supported stdio package launch option."""

    manager: str
    command: str
    args: list[str] = Field(default_factory=list)
    package: str | None = None


class MCPConfigField(BaseModel):
    """Typed configure-dialog field declared by a catalog spec."""

    key: str
    label: str
    description: str
    target: MCPConnectionTarget
    required: bool = True
    secret: bool = False


class _MCPConnectionSpecBase(BaseModel):
    """Base fields shared by all catalog connection spec variants."""

    requires_config: bool = False
    credentials: list[MCPConnectionCredential] = Field(default_factory=list)

    @computed_field
    @property
    def config_fields(self) -> list[MCPConfigField]:
        """Configure-dialog view of ``credentials``; same data, UI field shape."""
        return [
            MCPConfigField(
                key=credential.key,
                label=credential.label,
                description=credential.description,
                target=credential.target,
                required=credential.required,
                secret=credential.secret,
            )
            for credential in self.credentials
        ]


class MCPHTTPOAuth2ConnectionSpec(_MCPConnectionSpecBase):
    """HTTP MCP server using MCP OAuth."""

    kind: Literal["http_oauth2"] = "http_oauth2"
    server_type: Literal["http"] = "http"
    auth_type: Literal[MCPAuthType.OAUTH2] = MCPAuthType.OAUTH2
    server_uri: str
    scopes: list[str] = Field(default_factory=list)
    oauth_authorization_endpoint: str | None = None
    """Known OAuth authorization endpoint pinned by the repo-owned catalog row.

    Some providers serve OAuth endpoints on a sibling host of the MCP server
    (e.g. incident.io's mcp.* metadata advertises app.* endpoints). Pinning
    the endpoint here marks its host as trusted during MCP OAuth discovery
    without relaxing same-host validation globally.
    """
    oauth_token_endpoint: str | None = None


class MCPHTTPCustomConnectionSpec(_MCPConnectionSpecBase):
    """HTTP MCP server using user-provided headers or API keys."""

    kind: Literal["http_custom"] = "http_custom"
    server_type: Literal["http"] = "http"
    auth_type: Literal[MCPAuthType.CUSTOM] = MCPAuthType.CUSTOM
    server_uri: str


class MCPHTTPNoneConnectionSpec(_MCPConnectionSpecBase):
    """HTTP MCP server with no authentication."""

    kind: Literal["http_none"] = "http_none"
    server_type: Literal["http"] = "http"
    auth_type: Literal[MCPAuthType.NONE] = MCPAuthType.NONE
    server_uri: str


class MCPStdioCustomConnectionSpec(_MCPConnectionSpecBase):
    """Stdio MCP server using user-provided env vars."""

    kind: Literal["stdio_custom"] = "stdio_custom"
    server_type: Literal["stdio"] = "stdio"
    auth_type: Literal[MCPAuthType.CUSTOM] = MCPAuthType.CUSTOM
    stdio_command: str | None = None
    stdio_args: list[str] = Field(default_factory=list)
    stdio_env: list[str] = Field(default_factory=list)
    packages: list[MCPPackageOption] = Field(default_factory=list)


class MCPStdioNoneConnectionSpec(_MCPConnectionSpecBase):
    """Stdio MCP server with no authentication."""

    kind: Literal["stdio_none"] = "stdio_none"
    server_type: Literal["stdio"] = "stdio"
    auth_type: Literal[MCPAuthType.NONE] = MCPAuthType.NONE
    stdio_command: str | None = None
    stdio_args: list[str] = Field(default_factory=list)
    stdio_env: list[str] = Field(default_factory=list)
    packages: list[MCPPackageOption] = Field(default_factory=list)


type MCPConnectionSpec = Annotated[
    MCPHTTPOAuth2ConnectionSpec
    | MCPHTTPCustomConnectionSpec
    | MCPHTTPNoneConnectionSpec
    | MCPStdioCustomConnectionSpec
    | MCPStdioNoneConnectionSpec,
    Field(discriminator="kind"),
]


class MCPConnectionOption(BaseModel):
    """A connectable transport/auth option for one catalog provider."""

    id: str = Field(..., min_length=1, max_length=80)
    label: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=512)
    docs_url: str | None = None
    connection_spec: MCPConnectionSpec


class PlatformMCPCatalogRead(BaseModel):
    """Catalog row joined with workspace-specific MCP state."""

    id: uuid.UUID
    slug: str
    name: str
    description: str
    category: str
    status: PlatformMCPCatalogStatus
    icon_url: str | None
    docs_url: str | None
    provider_id: str | None
    connection_spec: MCPConnectionSpec | None
    connection_options: list[MCPConnectionOption] = Field(default_factory=list)
    locked: bool = Field(
        description="Whether this platform MCP catalog row is locked by entitlement.",
    )
    state: PlatformMCPCatalogState
    mcp_integration_id: UUID4 | None
    mcp_server_type: MCPServerType | None = None
    mcp_auth_type: MCPAuthType | None = None
    created_at: datetime
    updated_at: datetime
    last_refreshed_at: datetime | None


class PlatformMCPCatalogListResponse(BaseModel):
    """Cursor-paginated platform MCP catalog response."""

    items: list[PlatformMCPCatalogRead]
    next_cursor: str | None = None


class MCPIntegrationRead(BaseModel):
    """Response model for MCP integration."""

    id: UUID4
    workspace_id: WorkspaceID
    name: str
    description: str | None
    slug: str
    # Server type
    server_type: MCPServerType
    # HTTP-type server fields
    server_uri: str | None
    auth_type: MCPAuthType
    oauth_integration_id: UUID4 | None
    state: PlatformMCPCatalogState
    # Stdio-type server fields
    stdio_command: str | None
    stdio_args: list[str] | None
    # NOTE: stdio_env is write-only to avoid exposing secrets in API responses
    has_stdio_env: bool = False
    """Whether stdio_env is configured (actual values are not exposed)."""
    # General fields
    timeout: int | None
    created_at: datetime
    updated_at: datetime


class MCPCatalogConnectResponse(BaseModel):
    """Response for connecting a platform MCP catalog entry."""

    status: Literal["connected", "oauth_redirect"]
    mcp_integration: MCPIntegrationRead | None = None
    auth_url: str | None = None
    provider_id: str | None = None
