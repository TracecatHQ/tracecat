from enum import StrEnum


class IntegrationStatus(StrEnum):
    """Status of an integration."""

    NOT_CONFIGURED = "not_configured"
    """The integration is not configured."""
    CONFIGURED = "configured"
    """The integration is configured but not connected."""
    CONNECTED = "connected"
    """The integration is connected."""


class OAuthGrantType(StrEnum):
    """Grant type for OAuth 2.0."""

    AUTHORIZATION_CODE = "authorization_code"
    """Authorization code grant type. See https://datatracker.ietf.org/doc/html/rfc6749#section-1.3.1"""
    CLIENT_CREDENTIALS = "client_credentials"
    """Client credentials grant type. See https://datatracker.ietf.org/doc/html/rfc6749#section-1.3.4"""


class MCPAuthType(StrEnum):
    """Authentication type for MCP integrations."""

    OAUTH2 = "OAUTH2"
    CUSTOM = "CUSTOM"
    NONE = "NONE"


class MCPDiscoveryStatus(StrEnum):
    """Discovery status for a persisted MCP integration catalog."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALE = "stale"


class MCPDiscoveryAttemptStatus(StrEnum):
    """Status of an individual MCP discovery attempt."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MCPDiscoveryTrigger(StrEnum):
    """Source that initiated an MCP discovery run."""

    CREATE = "create"
    UPDATE = "update"
    REFRESH = "refresh"


class MCPTransport(StrEnum):
    """Transport used to connect to a remote MCP server."""

    HTTP = "http"
    SSE = "sse"


class MCPCatalogArtifactType(StrEnum):
    """Supported MCP catalog artifact types."""

    TOOL = "tool"
    RESOURCE = "resource"
    PROMPT = "prompt"
