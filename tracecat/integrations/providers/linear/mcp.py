"""Linear MCP OAuth integration using Model Context Protocol."""

from typing import ClassVar

from tracecat.integrations.providers.base import MCPAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


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
    mcp_server_uri: ClassVar[str] = "https://mcp.linear.app/mcp"

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
        api_docs_url="https://linear.app/docs/mcp",
    )
