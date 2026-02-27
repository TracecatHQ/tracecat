"""Jira MCP OAuth integration using Model Context Protocol."""

from typing import ClassVar

from tracecat.integrations.providers.base import MCPAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class JiraMCPProvider(MCPAuthProvider):
    """Jira OAuth 2.1 provider for Model Context Protocol integration.

    This provider enables integration with Atlassian's remote MCP server for:
    - Querying and updating Jira issues and projects
    - Accessing contextual Atlassian cloud data from MCP-compatible agents
    - Running the browser-based OAuth 2.1 authorization flow

    OAuth endpoints are automatically discovered from the server.
    """

    id: ClassVar[str] = "jira_mcp"

    # Atlassian remote MCP endpoint - OAuth endpoints discovered automatically.
    mcp_server_uri: ClassVar[str] = "https://mcp.atlassian.com/v1/mcp"

    # No default scopes - authorization server determines based on user permissions.
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="jira_mcp",
        name="Jira MCP",
        description="Jira MCP provider for Atlassian cloud issue and project access",
        enabled=True,
        requires_config=False,
        setup_instructions=(
            "Connect to Jira MCP to authorize browser-based OAuth 2.1 access to your "
            "Atlassian cloud data. Permissions are automatically determined based on "
            "your Jira and Atlassian account access."
        ),
        api_docs_url=(
            "https://support.atlassian.com/atlassian-rovo-mcp-server/docs/"
            "getting-started-with-the-atlassian-remote-mcp-server/"
        ),
    )
