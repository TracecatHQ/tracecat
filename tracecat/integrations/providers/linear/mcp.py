"""Linear MCP OAuth integration using Model Context Protocol."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import MCPAuthProvider


class LinearMCPProvider(MCPAuthProvider):
    """Linear OAuth provider for Model Context Protocol integration.

    This provider enables integration with Linear's MCP server for:
    - Accessing and managing issues, projects, and teams
    - Running GraphQL queries against Linear's API
    - Automating workflows and issue management

    OAuth endpoints are automatically discovered from the server.
    """

    id: ClassVar[str] = "linear_mcp"

    # MCP server endpoint - OAuth endpoints discovered automatically
    _mcp_server_uri: ClassVar[str] = "https://mcp.linear.app/mcp"

    # No default scopes - authorization server determines based on user permissions
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="linear_mcp",
        name="Linear MCP",
        description="Linear MCP provider for issue tracking and project management",
        enabled=True,
        requires_config=False,
        setup_instructions=(
            "Connect to Linear MCP to access issues, projects, and teams. "
            "Permissions are automatically determined based on your Linear workspace access."
        ),
        setup_steps=[
            "Click 'Connect' to begin OAuth authorization",
            "Select your Linear workspace if prompted",
            "Review and approve the OAuth client permissions",
            "Complete authorization to enable MCP integration",
        ],
        api_docs_url="https://linear.app/docs/mcp",
    )
