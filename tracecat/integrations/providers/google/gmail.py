"""Google Gmail OAuth provider using authorization code flow.

This provider enables secure Gmail API access through OAuth 2.0 authorization code flow.
Users can connect their Google account through Tracecat's integration UI.
"""

from typing import Any, ClassVar

from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class GoogleGmailACProvider(AuthorizationCodeOAuthProvider):
    """Google Gmail OAuth provider using authorization code flow for user access.

    This provider enables Gmail API access for security automation workflows including:
    - Email search and retrieval
    - Phishing investigation
    - Message header analysis
    - Attachment inspection
    """

    id: ClassVar[str] = "google_gmail"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "https://www.googleapis.com/auth/gmail.labels",
            "https://www.googleapis.com/auth/gmail.metadata",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.readonly",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="google_gmail",
        name="Google Gmail",
        description="Gmail API access for email search, retrieval, and security investigations",
        requires_config=True,
        enabled=True,
        api_docs_url="https://developers.google.com/gmail/api/reference/rest",
        setup_guide_url="https://developers.google.com/gmail/api/quickstart/python",
        troubleshooting_url="https://developers.google.com/identity/protocols/oauth2/web-server#httprest",
    )

    # Google OAuth endpoints
    default_authorization_endpoint: ClassVar[str | None] = (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    default_token_endpoint: ClassVar[str | None] = "https://oauth2.googleapis.com/token"

    def _use_pkce(self) -> bool:
        """Enable PKCE for enhanced security (recommended by Google)."""
        return True

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add Google-specific authorization parameters."""
        params = super()._get_additional_authorize_params()
        # Request offline access to get refresh token
        params["access_type"] = "offline"
        # Force consent screen to ensure we get a refresh token
        params["prompt"] = "consent"
        return params
