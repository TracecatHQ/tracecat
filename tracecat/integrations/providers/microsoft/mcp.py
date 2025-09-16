"""Microsoft Learn MCP OAuth integration using Model Context Protocol."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import MCPAuthProvider


class MicrosoftLearnMCPProvider(MCPAuthProvider):
    """Microsoft Learn OAuth provider for Model Context Protocol integration.

    This provider enables integration with Microsoft Learn's MCP server for:
    - Real-time access to official Microsoft documentation
    - AI-powered documentation search and retrieval
    - Technical knowledge from Microsoft's documentation library

    Uses Microsoft Entra ID (Azure AD) for authentication.
    Uses fallback OAuth endpoints since discovery is not supported.
    """

    id: ClassVar[str] = "microsoft_learn_mcp"

    # MCP server endpoint
    _mcp_server_uri: ClassVar[str] = "https://learn.microsoft.com/api/mcp"

    # Microsoft Entra ID OAuth endpoints (fallback since discovery isn't supported)
    _fallback_auth_endpoint: ClassVar[str] = (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    )
    _fallback_token_endpoint: ClassVar[str] = (
        "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    )

    # No default scopes - authorization server determines based on user permissions
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft_learn_mcp",
        name="Microsoft Learn MCP",
        description="Microsoft Learn Model Context Protocol OAuth provider for AI-powered documentation access",
        enabled=True,
        requires_config=False,
        setup_instructions=(
            "Connect to Microsoft Learn MCP to access real-time Microsoft documentation. "
            "This integration provides AI assistance with official Microsoft technical documentation. "
            "Authentication is handled through Microsoft Entra ID (Azure AD)."
        ),
        setup_steps=[
            "Click 'Connect' to begin OAuth authorization",
            "Sign in with your Microsoft account",
            "Review and approve the OAuth permissions",
            "Complete authorization to enable Microsoft Learn MCP integration",
        ],
        api_docs_url="https://github.com/microsoft/mcp",
    )
