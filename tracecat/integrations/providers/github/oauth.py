"""GitHub OAuth provider using client credentials flow.

Note: This provider follows the same structure as Microsoft Graph's
client-credentials implementation to fit Tracecat's integration model.
It uses GitHub's OAuth token endpoint and requires a client ID and secret.

Intended usage: Acquire an access token to be used for HTTPS git operations
from UDFs, e.g. using:
  https://x-access-token:<token>@github.com/<owner>/<repo>.git
"""

from typing import ClassVar

from pydantic import BaseModel, Field

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import ClientCredentialsOAuthProvider


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


def _auth_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/login/oauth/authorize"


def _token_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/login/oauth/access_token"


CC_SCOPES = ProviderScopes(
    # Scopes commonly needed for repository clone and read operations
    # Adjust per your organization policy.
    default=["repo"],
)

CC_METADATA = ProviderMetadata(
    id="github",
    name="GitHub (Service account)",
    description=(
        "GitHub OAuth provider using client credentials for service account flows"
    ),
    setup_steps=[
        "Create an OAuth application in GitHub settings",
        "Copy Client ID and Client Secret",
        "Configure credentials in Tracecat",
        "Use the access token for HTTPS git clone in UDFs",
    ],
    enabled=True,
    api_docs_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps",
    setup_guide_url="https://docs.github.com/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app",
    troubleshooting_url="https://docs.github.com/authentication/troubleshooting-oauth-app-access-token-request-errors",
)


class GitHubCCProvider(ClientCredentialsOAuthProvider):
    """GitHub OAuth provider using client credentials for application access."""

    id: ClassVar[str] = "github"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    config_model: ClassVar[type[BaseModel]] = GitHubOAuthConfig
    metadata: ClassVar[ProviderMetadata] = CC_METADATA

    def __init__(self, *, base_url: str = "https://github.com", **kwargs):
        self._base_url = base_url
        # Set endpoints dynamically from base_url
        self._authorization_endpoint = _auth_endpoint(self._base_url)
        self._token_endpoint = _token_endpoint(self._base_url)
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        return self._authorization_endpoint

    @property
    def token_endpoint(self) -> str:
        return self._token_endpoint

