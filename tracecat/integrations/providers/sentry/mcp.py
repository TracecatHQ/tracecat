"""Sentry MCP OAuth integration using Model Context Protocol."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import MCPAuthProvider


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
    _mcp_server_uri: ClassVar[str] = "https://mcp.sentry.dev/mcp"

    # No default scopes - authorization server determines based on user permissions
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="sentry_mcp",
        name="Sentry MCP",
        description="Sentry Model Context Protocol OAuth provider for error tracking and performance monitoring",
        enabled=True,
        requires_config=False,
        setup_instructions=(
            "Connect to Sentry MCP to access error tracking, performance monitoring, and issue management. "
            "Permissions are automatically determined based on your Sentry organization access."
        ),
        setup_steps=[
            "Click 'Connect' to begin OAuth authorization",
            "Authenticate with your Sentry account",
            "Select your Sentry organization if prompted",
            "Review and approve the OAuth client permissions",
            "Complete authorization to enable MCP integration",
        ],
        api_docs_url="https://mcp.sentry.dev",
    )
