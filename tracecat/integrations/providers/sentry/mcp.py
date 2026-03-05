"""Sentry MCP OAuth integration using Model Context Protocol."""

from typing import Any, ClassVar

from tracecat.integrations.providers.base import MCPAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class SentryMCPProvider(MCPAuthProvider):
    """Sentry OAuth provider for Model Context Protocol integration.

    This provider enables integration with Sentry's MCP server for:
    - Accessing and managing error tracking and performance monitoring
    - Querying issues, events, and performance data
    - Managing projects, teams, and organizations
    - Analyzing error patterns and performance metrics

    OAuth endpoints are automatically discovered from the server.
    """

    id: ClassVar[str] = "sentry_mcp"

    # MCP server endpoint - OAuth endpoints discovered automatically
    mcp_server_uri: ClassVar[str] = "https://mcp.sentry.dev/mcp"

    # No default scopes - authorization server determines based on user permissions
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="sentry_mcp",
        name="Sentry MCP",
        description="Sentry MCP provider for issues tracking and performance monitoring",
        enabled=True,
        requires_config=False,
        setup_instructions=(
            "Connect to Sentry MCP to access issues and performance monitoring. "
            "Permissions are automatically determined based on your Sentry organization access."
        ),
        api_docs_url="https://docs.sentry.io/product/sentry-mcp/",
    )

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Use the MCP endpoint as the OAuth resource for Sentry."""

        params = super()._get_additional_authorize_params()
        params["resource"] = self.mcp_server_uri
        return params

    def _get_additional_token_params(self) -> dict[str, Any]:
        """Use the same MCP endpoint resource during token exchange."""

        params = super()._get_additional_token_params()
        params["resource"] = self.mcp_server_uri
        return params
