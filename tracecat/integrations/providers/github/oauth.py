"""GitHub OAuth provider using authorization code flow."""

from typing import ClassVar

from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class GitHubOAuthProvider(AuthorizationCodeOAuthProvider):
    """GitHub OAuth provider using authorization code flow for user access."""

    id: ClassVar[str] = "github"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=["repo"])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="github",
        name="GitHub (Delegated)",
        description="GitHub OAuth provider using authorization code flow for user access",
        requires_config=True,
        enabled=True,
        api_docs_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps",
        setup_guide_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app",
        troubleshooting_url="https://docs.github.com/en/apps/oauth-apps/maintaining-oauth-apps/troubleshooting-authorization-request-errors",
    )
    # Endpoints stay optional to respect BaseOAuthProvider's nullable defaults
    default_authorization_endpoint: ClassVar[str | None] = (
        "https://github.com/login/oauth/authorize"
    )
    default_token_endpoint: ClassVar[str | None] = (
        "https://github.com/login/oauth/access_token"
    )
