"""GitHub OAuth provider using authorization code flow."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider

GITHUB_AUTH_ENDPOINT = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_ENDPOINT = "https://github.com/login/oauth/access_token"
GITHUB_ENDPOINT_HELP = (
    "Default: GitHub.com endpoints. Replace the domain for GitHub Enterprise, e.g. "
    "https://github.mycompany.com/login/oauth/authorize and /login/oauth/access_token"
)


GITHUB_SCOPES = ProviderScopes(
    # Scopes commonly needed for repository clone and read operations
    # Adjust per your organization policy.
    default=["repo"],
)

GITHUB_METADATA = ProviderMetadata(
    id="github",
    name="GitHub (Delegated)",
    description=("GitHub OAuth provider using authorization code flow for user access"),
    setup_steps=[
        "Create an OAuth application in GitHub settings",
        "Set authorization callback URL to: {callback_url}",
        "Copy Client ID and Client Secret",
        "Configure client credentials and endpoints in Tracecat",
        "Complete OAuth flow to authorize access",
        "Use the access token for HTTPS git clone in UDFs",
    ],
    requires_config=True,
    enabled=True,
    api_docs_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps",
    setup_guide_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app",
    troubleshooting_url="https://docs.github.com/authentication/troubleshooting-oauth-app-access-token-request-errors",
)


class GitHubOAuthProvider(AuthorizationCodeOAuthProvider):
    """GitHub OAuth provider using authorization code flow for user access."""

    id: ClassVar[str] = "github"
    scopes: ClassVar[ProviderScopes] = GITHUB_SCOPES
    metadata: ClassVar[ProviderMetadata] = GITHUB_METADATA
    default_authorization_endpoint: ClassVar[str] = GITHUB_AUTH_ENDPOINT
    default_token_endpoint: ClassVar[str] = GITHUB_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | None] = GITHUB_ENDPOINT_HELP
    token_endpoint_help: ClassVar[str | None] = GITHUB_ENDPOINT_HELP
