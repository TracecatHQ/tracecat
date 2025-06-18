"""GitHub OAuth integration using generic OAuth provider."""

from typing import ClassVar

from tracecat.integrations.base import BaseOauthProvider


class GitHubOAuthProvider(BaseOauthProvider):
    """GitHub OAuth provider using generic OAuth implementation."""

    id: ClassVar[str] = "github"

    # GitHub OAuth endpoints
    authorization_endpoint: ClassVar[str] = "https://github.com/login/oauth/authorize"
    token_endpoint: ClassVar[str] = "https://github.com/login/oauth/access_token"

    # Default GitHub scopes
    default_scopes: ClassVar[list[str]] = [
        "read:user",
        "user:email",
        "repo",
        "workflow",
    ]

    def _get_additional_token_params(self) -> dict[str, str]:
        """Add GitHub-specific token parameters."""
        return {
            "Accept": "application/json",  # GitHub returns token in JSON format
        }
