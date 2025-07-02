"""GitHub OAuth integration using generic OAuth provider."""

from typing import ClassVar

from tracecat.integrations.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.models import (
    ProviderCategory,
    ProviderMetadata,
    ProviderScopes,
)


class GitHubOAuthProvider(AuthorizationCodeOAuthProvider):
    """GitHub OAuth provider using generic OAuth implementation."""

    id: ClassVar[str] = "github"

    # GitHub OAuth endpoints
    _authorization_endpoint: ClassVar[str] = "https://github.com/login/oauth/authorize"
    _token_endpoint: ClassVar[str] = "https://github.com/login/oauth/access_token"

    # GitHub OAuth scopes
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "read:user",
            "user:email",
            "repo",
            "workflow",
        ]
    )

    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="github",
        name="GitHub",
        description="GitHub OAuth provider for repository and workflow integration",
        categories=[ProviderCategory.AUTH],
        setup_steps=[
            "Go to GitHub Settings > Developer settings > OAuth Apps",
            "Click 'New OAuth App' or select existing app",
            "Add the redirect URI shown above as the Authorization callback URL",
            "Set required repository permissions",
            "Copy Client ID and Client Secret",
            "Configure credentials in Tracecat",
        ],
        enabled=False,
    )

    def _get_additional_token_params(self) -> dict[str, str]:
        """Add GitHub-specific token parameters."""
        return {
            "Accept": "application/json",  # GitHub returns token in JSON format
        }
