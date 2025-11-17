"""Notion MCP OAuth integration using Model Context Protocol."""

from typing import ClassVar

from tracecat.integrations.providers.base import MCPAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class NotionMCPProvider(MCPAuthProvider):
    """Notion OAuth provider for Model Context Protocol integration.

    This provider enables AI-powered integration with Notion workspaces for:
    - Reading and writing pages, databases, and comments
    - AI-optimized Markdown-based content retrieval
    - Dynamic workspace access based on user permissions

    OAuth endpoints are automatically discovered from the server.
    """

    id: ClassVar[str] = "notion_mcp"

    # MCP server endpoint - OAuth endpoints discovered automatically
    mcp_server_uri: ClassVar[str] = "https://mcp.notion.com/mcp"

    # No default scopes - authorization server determines based on user permissions
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="notion_mcp",
        name="Notion MCP",
        description="Notion MCP provider for Notion workspace access",
        enabled=True,
        requires_config=False,
        setup_instructions=(
            "Connect to Notion MCP to enable AI tools to interact with your Notion workspace. "
            "Full read and write access to pages, databases, and comments based on your permissions."
        ),
        setup_steps=[
            "Click 'Connect' to begin OAuth authorization",
            "Select your Notion workspace",
            "Review and approve the permissions",
            "Complete authorization to enable MCP integration",
        ],
        api_docs_url="https://developers.notion.com/docs/mcp",
    )
