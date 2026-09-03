"""Wiz MCP OAuth integration using Model Context Protocol."""

from typing import Any, ClassVar

from tracecat.integrations.providers.base import MCPAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class WizMCPProvider(MCPAuthProvider):
    """Wiz OAuth provider for Model Context Protocol integration.

    This provider enables integration with Wiz MCP for:
    - Querying Wiz cloud security graph context
    - Investigating findings, entities, and relationships with AI agents
    - Running OAuth-authenticated MCP requests against Wiz

    OAuth endpoints are automatically discovered from the server.
    """

    id: ClassVar[str] = "wiz_mcp"

    # MCP server endpoint - OAuth endpoints discovered automatically
    mcp_server_uri: ClassVar[str] = "https://mcp.app.wiz.io/"

    # No default scopes - authorization server determines based on user permissions
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="wiz_mcp",
        name="Wiz MCP",
        description="Wiz MCP provider for cloud security investigations",
        enabled=True,
        requires_config=False,
        setup_instructions=(
            "Connect to Wiz MCP to authorize AI agents against your Wiz environment. "
            "Permissions are automatically determined based on your Wiz account access."
        ),
        api_docs_url="https://mcp.app.wiz.io/",
    )

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        params = super()._get_additional_authorize_params()
        # Wiz requires the resource value to include the trailing slash.
        params["resource"] = self.mcp_server_uri
        return params

    def _get_additional_token_params(self) -> dict[str, Any]:
        params = super()._get_additional_token_params()
        # Keep token exchange resource aligned with the authorize request.
        params["resource"] = self.mcp_server_uri
        return params
