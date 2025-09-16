"""GitHub Copilot MCP OAuth integration using Model Context Protocol."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import MCPAuthProvider


class GitHubMCPProvider(MCPAuthProvider):
    """GitHub Copilot OAuth provider for Model Context Protocol integration.

    This provider enables integration with GitHub Copilot's MCP server for:
    - Code assistance and suggestions
    - Repository context understanding
    - Development workflow automation

    Uses fallback OAuth endpoints since GitHub doesn't support discovery.
    """

    id: ClassVar[str] = "github_mcp"

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
        requires_config=False,
        setup_instructions=(
            "Connect to GitHub Copilot MCP to enable AI-powered code assistance and repository context. "
            "Permissions are automatically determined based on your GitHub account and organization settings."
        ),
        setup_steps=[
            "Click 'Connect' to begin OAuth authorization",
            "Authenticate with your GitHub account",
            "Review and approve the OAuth client permissions",
            "Complete authorization to enable MCP integration",
        ],
        api_docs_url="https://docs.github.com/en/copilot",
    )
