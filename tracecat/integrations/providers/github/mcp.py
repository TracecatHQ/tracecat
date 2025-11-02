"""GitHub Copilot MCP OAuth integration using Model Context Protocol."""

from typing import ClassVar

from tracecat.integrations.providers.base import MCPAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class GitHubMCPProvider(MCPAuthProvider):
    """GitHub Copilot OAuth provider for Model Context Protocol integration.

    This provider enables integration with GitHub Copilot's MCP server for:
    - Code assistance and suggestions
    - Repository context understanding
    - Development workflow automation

    Uses fallback OAuth endpoints since GitHub doesn't support discovery.
    """

    id: ClassVar[str] = "github_mcp"
    token_endpoint_auth_methods_supported: ClassVar[list[str]] = ["none"]

    # MCP server endpoint
    _mcp_server_uri: ClassVar[str] = "https://api.githubcopilot.com/mcp"

    # Fallback OAuth endpoints (GitHub doesn't support discovery)
    _fallback_auth_endpoint: ClassVar[str] = "https://github.com/login/oauth/authorize"
    _fallback_token_endpoint: ClassVar[str] = (
        "https://github.com/login/oauth/access_token"
    )

    # No default scopes - authorization server determines based on user permissions
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])

    # Provider metadata
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="github_mcp",
        name="GitHub Copilot MCP",
        description="GitHub Copilot MCP provider for repo and code access",
        enabled=True,
        requires_config=True,
        setup_instructions=(
            "Connect to GitHub Copilot MCP to enable AI-powered code assistance and repository context. "
            "Register an OAuth application with GitHub Copilot MCP and provide the client ID and secret."
            "Permissions are automatically determined based on your GitHub account and organization settings."
        ),
        setup_steps=[
            "Register an OAuth client for GitHub Copilot MCP",
            "Configure the client ID and client secret in Tracecat",
            "Click 'Connect' to begin OAuth authorization",
            "Authenticate with your GitHub account",
            "Review and approve the OAuth client permissions",
            "Complete authorization to enable MCP integration",
        ],
        api_docs_url="https://docs.github.com/en/copilot",
    )
