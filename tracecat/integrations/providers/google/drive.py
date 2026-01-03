"""Google Drive OAuth provider using authorization code flow.

This provider enables secure Google Drive API access through OAuth 2.0 authorization code flow.
Users can connect their Google account through Tracecat's integration UI.
"""

from typing import Any, ClassVar

from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class GoogleDriveACProvider(AuthorizationCodeOAuthProvider):
    """Google Drive OAuth provider using authorization code flow for user access.

    This provider enables Drive API access for security automation workflows including:
    - File and folder management
    - Permission auditing and enforcement
    - Data loss prevention
    - Compliance automation
    """

    id: ClassVar[str] = "google_drive"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive.metadata",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="google_drive",
        name="Google Drive",
        description="Google Drive API access for file management, permissions, and security automation",
        requires_config=True,
        enabled=True,
        api_docs_url="https://developers.google.com/drive/api/reference/rest/v3",
        setup_guide_url="https://developers.google.com/drive/api/quickstart/python",
        troubleshooting_url="https://developers.google.com/drive/api/guides/handle-errors",
    )

    # Google OAuth endpoints (keep optional to mirror BaseOAuthProvider defaults)
    default_authorization_endpoint: ClassVar[str | None]
    default_authorization_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    default_token_endpoint: ClassVar[str | None]
    default_token_endpoint = "https://oauth2.googleapis.com/token"

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
