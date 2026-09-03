"""RunReveal MCP OAuth integration using Model Context Protocol."""

from typing import ClassVar

from tracecat.integrations.providers.base import MCPAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class RunRevealMCPProvider(MCPAuthProvider):
    """RunReveal OAuth provider for Model Context Protocol integration.

    This provider enables integration with RunReveal's MCP server for:
    - Running queries and detections
    - Accessing table schemas
    - Managing detection configurations

    Permissions are determined by the user's role in the selected workspace.
    OAuth endpoints are automatically discovered from the server.
    """

    id: ClassVar[str] = "runreveal_mcp"

    # MCP server endpoint - OAuth endpoints discovered automatically
    mcp_server_uri: ClassVar[str] = "https://api.runreveal.com/mcp"

    # RunReveal's MCP resource server is api.runreveal.com, while OAuth endpoints
    # discovered from it are served from www-api.runreveal.com.
    oauth_endpoint_allowed_hosts: ClassVar[frozenset[str]] = frozenset(
        {"www-api.runreveal.com"}
    )

    # No default scopes - authorization server determines based on user/workspace permissions
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="runreveal_mcp",
        name="RunReveal MCP",
        description="RunReveal MCP provider for security data analysis",
        enabled=True,
        requires_config=False,
        setup_instructions=(
            "Connect to RunReveal MCP to access queries, detections, and table schemas. "
            "Permissions are automatically determined based on your workspace role."
        ),
        api_docs_url="https://docs.runreveal.com/ai-chat/model-context-protocol",
    )
