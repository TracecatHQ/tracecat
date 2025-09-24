"""GitHub OAuth provider using authorization code flow.

Note: This provider uses GitHub's OAuth authorization code flow which requires
user authorization through a browser redirect. The acquired access token can
be used for HTTPS git operations from UDFs, e.g. using:
  https://x-access-token:<token>@github.com/<owner>/<repo>.git
"""

from typing import ClassVar

from pydantic import BaseModel, Field

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider


class GitHubOAuthConfig(BaseModel):
    """Configuration model for GitHub OAuth provider.

    For GitHub Enterprise Server, set ``base_url`` to your enterprise hostname.
    """

    base_url: str = Field(
        default="https://github.com",
        description=(
            "Base URL for GitHub. Use your enterprise hostname, e.g. "
            "'https://github.mycompany.com' for GHES."
        ),
        min_length=8,
        max_length=200,
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
        "Configure credentials in Tracecat",
        "Complete OAuth flow to authorize access",
        "Use the access token for HTTPS git clone in UDFs",
    ],
    enabled=True,
    api_docs_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps",
    setup_guide_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app",
    troubleshooting_url="https://docs.github.com/authentication/troubleshooting-oauth-app-access-token-request-errors",
)

GH_BASE_URL = "https://github.com"


class GitHubOAuthProvider(AuthorizationCodeOAuthProvider):
    """GitHub OAuth provider using authorization code flow for user access."""

    id: ClassVar[str] = "github"
    scopes: ClassVar[ProviderScopes] = GITHUB_SCOPES
    config_model: ClassVar[type[BaseModel]] = GitHubOAuthConfig
    metadata: ClassVar[ProviderMetadata] = GITHUB_METADATA
    _authorization_endpoint: ClassVar[str] = f"{GH_BASE_URL}/login/oauth/authorize"
    _token_endpoint: ClassVar[str] = f"{GH_BASE_URL}/login/oauth/access_token"
