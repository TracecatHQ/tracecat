"""Perplexity MCP OAuth integration using Model Context Protocol."""

from typing import Any, ClassVar

from tracecat.integrations.providers.base import MCPAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class PerplexityMCPProvider(MCPAuthProvider):
    """Perplexity OAuth provider for Model Context Protocol integration.

    This provider enables integration with Perplexity's hosted MCP server for:
    - Real-time web search with citations
    - Conversational answers grounded in search results
    - Deep research and multi-step reasoning

    OAuth endpoints are automatically discovered from the server.
    """

    id: ClassVar[str] = "perplexity_mcp"

    # MCP server endpoint - OAuth endpoints discovered automatically
    mcp_server_uri: ClassVar[str] = "https://api.perplexity.ai/mcp"

    # Perplexity advertises this resource in its protected resource metadata
    # (without the /mcp path), so the token audience must match it.
    oauth_resource: ClassVar[str] = "https://api.perplexity.ai"

    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["perplexity_api", "offline_access"]
    )

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="perplexity_mcp",
        name="Perplexity MCP",
        description="Perplexity MCP provider for web search, research, and reasoning",
        enabled=True,
        requires_config=False,
        setup_instructions=(
            "Connect to Perplexity MCP to run web search, ask, research, and "
            "reasoning tools. Sign in with your Perplexity account and choose the "
            "API organization to bill."
        ),
        api_docs_url="https://docs.perplexity.ai/docs/getting-started/integrations/mcp-server",
    )

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Use Perplexity's advertised protected resource as the OAuth resource."""

        params = super()._get_additional_authorize_params()
        params["resource"] = self.oauth_resource
        return params

    def _get_additional_token_params(self) -> dict[str, Any]:
        """Use the same resource during token exchange."""

        params = super()._get_additional_token_params()
        params["resource"] = self.oauth_resource
        return params
