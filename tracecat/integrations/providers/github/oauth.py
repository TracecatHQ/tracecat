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
        setup_steps=[
            "Create an OAuth application in GitHub settings",
            "Set the authorization callback URL",
            "Copy Client ID and Client Secret",
            "Configure client credentials and endpoints in Tracecat",
            "Complete OAuth flow to authorize access",
            "Use the access token for HTTPS git clone in UDFs",
        ],
        requires_config=True,
        enabled=True,
        api_docs_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps",
        setup_guide_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app",
        troubleshooting_url="https://docs.github.com/en/apps/oauth-apps/maintaining-oauth-apps/troubleshooting-authorization-request-errors",
    )
    default_authorization_endpoint: ClassVar[str] = (
        "https://github.com/login/oauth/authorize"
    )
    default_token_endpoint: ClassVar[str] = (
        "https://github.com/login/oauth/access_token"
    )
