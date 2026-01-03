"""Secure Annex MCP OAuth integration using Model Context Protocol."""

from typing import ClassVar

from tracecat.integrations.providers.base import MCPAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class SecureAnnexMCPProvider(MCPAuthProvider):
    """Secure Annex OAuth provider for Model Context Protocol integration.

    This provider enables integration with Secure Annex's MCP server for:
    - Investigating browser and code editor extensions

    OAuth endpoints are automatically discovered from the server.
    """

    id: ClassVar[str] = "secureannex_mcp"

    # MCP server endpoint - OAuth endpoints discovered automatically
    mcp_server_uri: ClassVar[str] = "https://mcp.secureannex.com/mcp"

    # No default scopes - authorization server determines based on user/workspace permissions
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="secureannex_mcp",
        name="Secure Annex MCP",
        description="Secure Annex MCP provider for browser and code editor extension investigation",
        enabled=True,
        requires_config=False,
        setup_instructions=(
            "Connect to Secure Annex MCP to access browser and code editor extension investigation."
        ),
        api_docs_url="https://docs.secureannex.com/guides/mcp",
    )
