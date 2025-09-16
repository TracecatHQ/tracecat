"""Atlassian MCP OAuth integration using Model Context Protocol."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import MCPAuthProvider


class AtlassianMCPProvider(MCPAuthProvider):
    """Atlassian OAuth provider for Model Context Protocol integration.

    This provider enables integration with Atlassian's MCP server for:
    - Jira: Search, create/update issues, bulk operations
    - Confluence: Summarize pages, create content, navigate spaces
    - Compass: Create components, query dependencies, manage service landscape
    - Combined tasks across all three products

    Permissions are based on the user's existing Atlassian Cloud access.
    All actions respect existing project or space-level roles.
    OAuth endpoints are automatically discovered from the server.
    """

    id: ClassVar[str] = "atlassian_mcp"

    # MCP server endpoint - OAuth endpoints discovered automatically
    _mcp_server_uri: ClassVar[str] = "https://mcp.atlassian.com/v1/sse"

    # No default scopes - authorization server determines based on user permissions
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="atlassian_mcp",
        name="Atlassian MCP",
        description=(
            "Atlassian Model Context Protocol OAuth provider for "
            "Jira, Confluence, and Compass integration"
        ),
        enabled=True,
        requires_config=False,
        setup_instructions=(
            "Connect to Atlassian MCP to access Jira, Confluence, and Compass. "
            "Access is granted only to data you already have permission to view in "
            "Atlassian Cloud. All actions respect existing project or space-level roles."
        ),
        setup_steps=[
            "Click 'Connect' to begin OAuth authorization",
            "Authenticate with your Atlassian account",
            "Review and approve the OAuth permissions",
            "Complete authorization to enable MCP integration with Jira, Confluence, and Compass",
        ],
        api_docs_url=(
            "https://support.atlassian.com/atlassian-rovo-mcp-server/docs/"
            "getting-started-with-the-atlassian-remote-mcp-server/"
        ),
    )
